"""Automatic load balancing (docs/auto-balance-design.md).

On each worker cycle, for every cluster whose mode is "recommend" or "auto_approve", decide whether the cluster is out
of balance enough (per the chosen aggressiveness level) and, if so, create ONE normal cluster.rebalance operation in
awaiting_approval, created by "PyXie (automatic)".

- recommend: a person reviews and approves it exactly like a manual plan; this module never approves or runs anything.
- auto_approve: the plan holds exactly ONE move. If it passes the extra gates below, the system approves it and queues
  it; when it finishes, the NEXT move is planned from fresh cluster data after the level's cooldown, never from a stale
  multi-move plan. A failed or blocked move stops everything and drops the cluster back to recommend.

Same planner, same hard blocks, same Safety Contract as Balance Load: the approval is a new caller of the normal path.
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .audit import write_audit_event
from .balance_config import (
    PRESETS, balance_score, get_config, get_state, hours_ago, in_window, resolve_metric, save_config, save_state, utcnow,
)
from .balance_workflow import approve as approve_balance, dry_run_balance
from .models import AppSettings, Cluster, Node, Operation, ResourceLock, Workload
from .notifications import dispatch_event
from .operations_engine import enter_stage

log = logging.getLogger(__name__)

AUTOMATIC_ACTOR = "PyXie (automatic)"
LIVE_STATUSES = ("pending", "dry_run", "awaiting_approval", "approved", "revalidating", "executing", "monitoring", "verifying")
STALE_PLAN_HOURS = 12.0  # a plan nobody approved is replaced by a fresh one rather than left to block automatic balancing
AUTO_APPROVE_MEMORY_CEILING_PCT = 80.0  # an auto-approved move may not leave any node fuller than this
AUTO_APPROVE_MOVES_PER_RUN = 1  # one move at a time, then re-evaluate from fresh data


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


def auto_approve_blockers(
    migrate_plan: list[dict], projected: list[dict], *, writes_enabled: bool, busy_nodes: bool,
    ceiling_pct: float = AUTO_APPROVE_MEMORY_CEILING_PCT,
) -> list[str]:
    """Why an automatic plan must be left for a person instead of being approved by the system. Empty = may approve.
    Pure: no database."""
    reasons: list[str] = []
    moves = [m for m in migrate_plan if m.get("transport") != "skip"]
    if not moves:
        reasons.append("the plan has no moves")
    if not writes_enabled:
        reasons.append("the global write switch is off")
    if any(m.get("transport", "live") != "live" for m in moves):
        reasons.append("a move is not a live migration")
    if any(m.get("destination_storage_id") for m in moves):
        reasons.append("a move also changes storage")
    over = [p for p in projected if (p.get("after_pct") or 0) > ceiling_pct]
    if over:
        reasons.append(f"it would leave {over[0]['name']} above {round(ceiling_pct)}% memory")
    if busy_nodes:
        reasons.append("a node involved is busy with another operation")
    return reasons


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


def _stop_if_last_failed(db: Session, cluster: Cluster, cfg: dict, state: dict, now) -> dict | None:
    """The first failed or blocked automatic move ends auto-approve: the cluster drops back to recommend until a person
    turns it on again (with the confirmation), and an alert goes out. Nothing retries unattended."""
    op_id = state.get("operation_id")
    if not (state.get("auto_approved") and op_id) or state.get("failure_handled") == op_id:
        return None
    op = db.query(Operation).filter(Operation.id == op_id).one_or_none()
    if op is None or op.status not in ("failed", "blocked"):
        return None
    why = (op.error or ", ".join(op.blocking_safety_rules or []) or op.status).strip()
    reason = f"the last automatic move {op.status}: {why[:200]}"
    save_config(db, cluster.id, {**cfg, "mode": "recommend"})
    save_state(db, cluster.id, {**state, "failure_handled": op_id, "last_checked_at": now.isoformat(), "last_result": "stopped", "last_reason": reason})
    write_audit_event(
        db, event_category="operation", event_type="balance.auto_stopped", actor=AUTOMATIC_ACTOR, actor_type="system",
        metadata={"cluster": cluster.name, "operation_id": str(op.id), "status": op.status, "reason": reason},
    )
    dispatch_event(
        db, severity="critical", category="balance", recovered=False, observed_at=now,
        title=f"Automatic balancing stopped on {cluster.name}: {reason}. It is back to Recommend only; turn auto-approve on again after you have looked.",
    )
    return {"cluster": cluster.name, "result": "stopped", "reason": reason}


def _nodes_busy(db: Session, node_ids: set, now) -> bool:
    if not node_ids:
        return False
    return (
        db.query(ResourceLock)
        .filter(ResourceLock.resource_type == "node", ResourceLock.resource_id.in_(list(node_ids)),
                ResourceLock.released_at.is_(None), ResourceLock.expires_at > now)
        .count() > 0
    )


def evaluate_cluster(db: Session, cluster: Cluster, *, now=None, enqueue=None) -> dict:
    """enqueue(op) puts an approved operation on the worker's operations queue; auto-approve needs it."""
    now = now or utcnow()
    cfg = get_config(db, cluster.id)
    state = get_state(db, cluster.id)
    if cfg["mode"] not in ("recommend", "auto_approve"):
        return {"cluster": cluster.name, "result": "off"}
    auto = cfg["mode"] == "auto_approve"
    preset = PRESETS[cfg["level"]]

    if auto:
        stopped = _stop_if_last_failed(db, cluster, cfg, state, now)
        if stopped:
            return stopped

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

    # The cooldown runs from when the last auto-approved move FINISHED, so the next one always sees settled numbers.
    last = state.get("last_run_at")
    if auto and state.get("auto_approved") and state.get("operation_id"):
        prev = db.query(Operation).filter(Operation.id == state["operation_id"]).one_or_none()
        if prev is not None and prev.completed_at is not None:
            last = prev.completed_at.isoformat()
    since_run = hours_ago(last, now)
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
    if auto and cfg["level"] == "conservative":  # the gentlest level never touches guests that cannot tolerate downtime
        skip_ids |= {w.id for w in db.query(Workload.id).filter(Workload.cluster_id == cluster.id, Workload.downtime_tolerance == "low").all()}
    max_moves = AUTO_APPROVE_MOVES_PER_RUN if auto else preset["max_moves"]
    op = dry_run_balance(
        db, actor=AUTOMATIC_ACTOR, node_ids=list(node_ids) if node_ids else None, cluster_id=cluster.id,
        extra_context={
            "automatic": True, "cluster_id": str(cluster.id), "level": cfg["level"], "metric": metric,
            "metric_setting": cfg["metric"], "trigger_score": preset["trigger_score"], "balance_score": round(score),
            "min_benefit": preset["min_benefit"], "max_moves": max_moves, "auto_mode": cfg["mode"],
        },
        min_improvement=preset["min_benefit"], max_moves=max_moves, skip_workload_ids=skip_ids, only_if_moves=True,
    )
    if op is None:
        return _skip(db, cluster, state, now, "no move is worth making at this level", balance_score=round(score))

    plan = (op.dry_run_result or {}).get("migrate_plan", [])
    moves = len(plan)
    approved = False
    left_for_review = None
    if auto:
        involved = {m["destination_node_id"] for m in plan} | {m["source_node_id"] for m in plan if m.get("source_node_id")}
        writes = bool(db.query(AppSettings.pve_mutations_enabled).filter(AppSettings.id == 1).scalar())
        blockers = auto_approve_blockers(
            plan, (op.dry_run_result or {}).get("projected_memory", []), writes_enabled=writes,
            busy_nodes=_nodes_busy(db, {_uuid(i) for i in involved}, now),
        )
        if enqueue is None:
            blockers.append("the worker queue is not available")
        if blockers:
            left_for_review = "; ".join(blockers)
        else:
            op.context = {**(op.context or {}), "auto_approved": True}
            db.commit()
            op = approve_balance(db, op, approved_by=AUTOMATIC_ACTOR)
            enqueue(op)
            approved = True

    save_state(db, cluster.id, {
        "last_run_at": now.isoformat(), "last_checked_at": now.isoformat(),
        "last_result": "plan_auto_approved" if approved else "plan_created",
        "last_reason": f"left for your approval: {left_for_review}" if left_for_review else None,
        "operation_id": str(op.id), "moves": moves, "balance_score": round(score), "metric": metric, "auto_approved": approved,
    })
    what = ", ".join(f"{m['name']} {m['source_node']} -> {m['destination_node']}" for m in plan[:3])
    write_audit_event(
        db, event_category="operation", event_type="balance.auto_plan_approved" if approved else "balance.auto_plan_created",
        actor=AUTOMATIC_ACTOR, actor_type="system",
        metadata={"cluster": cluster.name, "operation_id": str(op.id), "moves": moves, "level": cfg["level"], "metric": metric,
                  "balance_score": round(score), "auto_approved": approved, "left_for_review": left_for_review, "plan": what},
    )
    if approved:
        title = f"Automatic balancing is moving {what} on {cluster.name} (balance score {round(score)}). It re-checks the cluster after this move finishes."
    else:
        title = f"Automatic Balance Load plan ready for {cluster.name}: {moves} move(s), balance score {round(score)}. Review it on the Balance Load page."
        if left_for_review:
            title += f" Not approved automatically because {left_for_review}."
    dispatch_event(db, severity="warning", category="balance", recovered=False, observed_at=now, title=title)
    return {"cluster": cluster.name, "result": "plan_auto_approved" if approved else "plan_created", "moves": moves, "operation_id": str(op.id)}


def _uuid(value):
    import uuid
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def evaluate_all(db: Session, enqueue=None) -> list[dict]:
    out = []
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all():
        try:
            out.append(evaluate_cluster(db, cluster, enqueue=enqueue))
        except Exception as e:  # noqa: BLE001 -- one cluster failing must not stop the others
            log.exception("automatic balance check failed for %s", cluster.name)
            db.rollback()
            out.append({"cluster": cluster.name, "result": "error", "reason": str(e)})
    return out
