"""Automatic load balancing, phase 1: RECOMMEND ONLY (docs/auto-balance-design.md).

On each worker cycle, for every cluster whose mode is "recommend", decide whether the cluster is out of balance
enough (per the chosen aggressiveness level) and, if so, create ONE normal cluster.rebalance operation in
awaiting_approval, created by "PyXie (automatic)". A person reviews and approves it exactly like a manual plan; this
module never approves or executes anything. Same planner, same hard blocks, same Safety Contract as Balance Load.
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .audit import write_audit_event
from .balance_config import (
    PRESETS, balance_score, get_config, get_state, hours_ago, in_window, resolve_metric, save_state, utcnow,
)
from .balance_workflow import dry_run_balance
from .models import AppSettings, Cluster, Node, Operation
from .notifications import dispatch_event
from .operations_engine import enter_stage

log = logging.getLogger(__name__)

AUTOMATIC_ACTOR = "PyXie (automatic)"
LIVE_STATUSES = ("pending", "dry_run", "awaiting_approval", "approved", "revalidating", "executing", "monitoring", "verifying")
STALE_PLAN_HOURS = 12.0  # a plan nobody approved is replaced by a fresh one rather than left to block automatic balancing


def stale_waiting_plan(status: str, context: dict | None, age_hours: float, cluster_id) -> bool:
    """Is this waiting plan old enough to be cleared out of the way of an automatic run?
    An automatic plan counts for its own cluster only. A Balance Load plan someone previewed and walked away from
    counts too. A Bulk Migrate plan never does: someone picked those exact guests on purpose."""
    ctx = context or {}
    if status != "awaiting_approval" or age_hours < STALE_PLAN_HOURS or ctx.get("mode") == "bulk_migrate":
        return False
    if ctx.get("automatic"):
        return ctx.get("cluster_id") == str(cluster_id)
    return True


def _live_rebalance_ops(db: Session) -> list[Operation]:
    return (
        db.query(Operation)
        .filter(Operation.operation_type_id == "cluster.rebalance", Operation.status.in_(LIVE_STATUSES), Operation.dismissed.is_(False))
        .all()
    )


def _recently_moved(db: Session, cluster_id, cooldown_hours: float, now) -> set:
    since = now - timedelta(hours=cooldown_hours)
    rows = (
        db.query(Operation.workload_id)
        .filter(
            Operation.operation_type_id == "vm.live_migrate", Operation.cluster_id == cluster_id,
            Operation.status == "completed", Operation.completed_at >= since, Operation.workload_id.isnot(None),
        )
        .all()
    )
    return {r[0] for r in rows}


def _skip(db: Session, cluster: Cluster, state: dict, now, reason: str, **extra) -> dict:
    state = {**state, "last_checked_at": now.isoformat(), "last_result": "skipped", "last_reason": reason, **extra}
    save_state(db, cluster.id, state)
    return {"cluster": cluster.name, "result": "skipped", "reason": reason}


def evaluate_cluster(db: Session, cluster: Cluster, *, now=None) -> dict:
    now = now or utcnow()
    cfg = get_config(db, cluster.id)
    state = get_state(db, cluster.id)
    if cfg["mode"] != "recommend":
        return {"cluster": cluster.name, "result": "off"}
    preset = PRESETS[cfg["level"]]

    if cfg.get("paused_until"):
        left = hours_ago(cfg["paused_until"], now)
        if left is not None and left < 0:
            return _skip(db, cluster, state, now, "paused")
    tz_name = (db.query(AppSettings.timezone).filter(AppSettings.id == 1).scalar()) or "UTC"
    try:
        local_now = now.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 -- an unknown timezone name must not stop balancing checks
        local_now = now
    if not in_window(cfg["windows"], local_now):
        return _skip(db, cluster, state, now, "outside the allowed time window")
    if cluster.quorate is False:
        return _skip(db, cluster, state, now, "cluster is not quorate")

    since_run = hours_ago(state.get("last_run_at"), now)
    if since_run is not None and since_run < preset["cluster_cooldown_hours"]:
        return _skip(db, cluster, state, now, "cooling down after the last automatic plan")

    for op in _live_rebalance_ops(db):
        automatic = bool((op.context or {}).get("automatic"))
        age = hours_ago(op.created_at.isoformat(), now) or 0
        if stale_waiting_plan(op.status, op.context, age, cluster.id):
            kind = "automatic" if automatic else "Balance Load"
            enter_stage(db, op, status="cancelled", error=f"Replaced: nobody approved this {kind} plan within {int(STALE_PLAN_HOURS)} hours.", actor=AUTOMATIC_ACTOR)
            op.dismissed = True
            db.commit()
            continue
        return _skip(db, cluster, state, now, "a Balance Load plan is already waiting or running")

    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).all()
    score = balance_score(nodes)
    if score is None:
        return _skip(db, cluster, state, now, "not enough nodes with live data")
    if score >= preset["trigger_score"]:
        return _skip(db, cluster, state, now, f"balanced enough (score {round(score)}, acts below {preset['trigger_score']})", balance_score=round(score))

    metric = resolve_metric(cfg["metric"], nodes)
    node_ids = {n.id for n in nodes if str(n.id) in set(cfg["node_ids"])} or None
    skip_ids = _recently_moved(db, cluster.id, preset["guest_cooldown_hours"], now)
    op = dry_run_balance(
        db, actor=AUTOMATIC_ACTOR, node_ids=list(node_ids) if node_ids else None, cluster_id=cluster.id,
        extra_context={
            "automatic": True, "cluster_id": str(cluster.id), "level": cfg["level"], "metric": metric,
            "metric_setting": cfg["metric"], "trigger_score": preset["trigger_score"], "balance_score": round(score),
            "min_benefit": preset["min_benefit"], "max_moves": preset["max_moves"],
        },
        min_improvement=preset["min_benefit"], max_moves=preset["max_moves"], skip_workload_ids=skip_ids, only_if_moves=True,
    )
    if op is None:
        return _skip(db, cluster, state, now, "no move is worth making at this level", balance_score=round(score))

    moves = len((op.dry_run_result or {}).get("migrate_plan", []))
    save_state(db, cluster.id, {
        "last_run_at": now.isoformat(), "last_checked_at": now.isoformat(), "last_result": "plan_created",
        "last_reason": None, "operation_id": str(op.id), "moves": moves, "balance_score": round(score), "metric": metric,
    })
    write_audit_event(
        db, event_category="operation", event_type="balance.auto_plan_created", actor=AUTOMATIC_ACTOR, actor_type="system",
        metadata={"cluster": cluster.name, "operation_id": str(op.id), "moves": moves, "level": cfg["level"],
                  "metric": metric, "balance_score": round(score)},
    )
    dispatch_event(
        db, severity="warning", category="balance", recovered=False, observed_at=now,
        title=f"Automatic Balance Load plan ready for {cluster.name}: {moves} move(s), balance score {round(score)}. Review it on the Balance Load page.",
    )
    return {"cluster": cluster.name, "result": "plan_created", "moves": moves, "operation_id": str(op.id)}


def evaluate_all(db: Session) -> list[dict]:
    out = []
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all():
        try:
            out.append(evaluate_cluster(db, cluster))
        except Exception as e:  # noqa: BLE001 -- one cluster failing must not stop the others
            log.exception("automatic balance check failed for %s", cluster.name)
            db.rollback()
            out.append({"cluster": cluster.name, "result": "error", "reason": str(e)})
    return out
