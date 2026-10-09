"""Settings and status for automatic load balancing (phase 1: recommend only). See docs/auto-balance-design.md."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.notifications import dispatch_event
from pyxie_core.auto_balance import AUTOMATIC_ACTOR, LIVE_STATUSES
from pyxie_core.balance_config import (
    PRESETS, ConfigError, balance_score, get_config, get_state, resolve_metric, save_config, save_state, smoothed_loads, smoothed_view, utcnow,
)
from pyxie_core.models import AppSettings, Cluster, Node, Operation, Workload

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/auto-balance", tags=["auto-balance"], dependencies=[Depends(get_current_user)])


def _locked_guests(db: Session, cluster: Cluster) -> dict:
    """Running VMs automatic balancing will never touch: marked Do not move, or sitting on the host they are pinned to."""
    vms = db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False), Workload.status == "running", Workload.type == "vm").all()
    return {
        "do_not_move": sum(1 for w in vms if w.do_not_move),
        "pinned": sum(1 for w in vms if not w.do_not_move and w.preferred_node_id is not None and w.preferred_node_id == w.node_id),
    }


def _cluster_view(db: Session, cluster: Cluster) -> dict:
    cfg = get_config(db, cluster.id)
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()
    score = balance_score(nodes)
    loads, history_ok = smoothed_loads(db, nodes, utcnow())
    busy = balance_score(smoothed_view(nodes, loads)) if history_ok else None
    pending = [
        o for o in db.query(Operation).filter(
            Operation.operation_type_id == "cluster.rebalance", Operation.status.in_(LIVE_STATUSES), Operation.dismissed.is_(False)).all()
        if (o.context or {}).get("automatic") and (o.context or {}).get("cluster_id") == str(cluster.id)
    ]
    waiting = [
        o for o in db.query(Operation).filter(
            Operation.operation_type_id == "cluster.rebalance", Operation.status.in_(LIVE_STATUSES), Operation.dismissed.is_(False))
        .order_by(Operation.created_at.desc()).all()
        if (o.context or {}).get("mode") != "bulk_migrate"
    ]
    recent = [
        o for o in db.query(Operation).filter(Operation.operation_type_id == "cluster.rebalance", Operation.created_by == AUTOMATIC_ACTOR)
        .order_by(Operation.created_at.desc()).limit(40).all()
        if (o.context or {}).get("cluster_id") == str(cluster.id)
    ][:10]
    return {
        "cluster_id": str(cluster.id), "cluster_name": cluster.name, "config": cfg, "state": get_state(db, cluster.id),
        "preset": PRESETS[cfg["level"]], "presets": PRESETS,
        "nodes": [{"id": str(n.id), "name": n.name} for n in nodes],
        "balance_score": None if score is None else round(score),
        "busy_score": None if busy is None else round(busy),  # the 7-day busy-level score automatic balancing actually acts on
        "history_ok": history_ok,
        "resolved_metric": resolve_metric(cfg["metric"], nodes),
        "pending_operation_id": str(pending[0].id) if pending else None,
        "writes_enabled": bool(db.query(AppSettings.pve_mutations_enabled).filter(AppSettings.id == 1).scalar()),
        "locked_guests": _locked_guests(db, cluster),
        "waiting_operation_id": str(waiting[0].id) if waiting else None,
        "history": [
            {"operation_id": str(o.id), "created_at": o.created_at.isoformat(), "status": "dismissed" if o.dismissed else o.status,
             "moves": len((o.dry_run_result or {}).get("migrate_plan", [])), "level": (o.context or {}).get("level"), "auto_approved": bool((o.context or {}).get("auto_approved")),
             "balance_score": (o.context or {}).get("balance_score")}
            for o in recent
        ],
    }


@router.get("")
def list_auto_balance(db: Session = Depends(get_db)):
    return [_cluster_view(db, c) for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).all()]


class AutoBalanceUpdate(BaseModel):
    mode: str = "off"
    level: str = "moderate"
    metric: str = "most_limited"
    node_ids: list[str] = []
    windows: list[dict] = []
    # Auto-approve only: the admin's confirmation (and, for Aggressive, the second one).
    acknowledged: bool = False
    acknowledged_aggressive: bool = False


def _cluster_or_404(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


@router.put("/{cluster_id}", dependencies=[Depends(require_admin)])
def update_auto_balance(cluster_id: uuid.UUID, payload: AutoBalanceUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cluster = _cluster_or_404(db, cluster_id)
    before = get_config(db, cluster_id)
    valid_nodes = {str(n.id) for n in db.query(Node).filter(Node.cluster_id == cluster_id).all()}
    if any(i not in valid_nodes for i in payload.node_ids):
        raise HTTPException(400, "node_ids must be nodes in this cluster")
    cfg_in = {**payload.model_dump(exclude={"acknowledged", "acknowledged_aggressive"}), "paused_until": before.get("paused_until")}
    if payload.mode == "auto_approve":
        prev = before.get("ack") if before["mode"] == "auto_approve" else None
        if payload.acknowledged:
            cfg_in["ack"] = {"by": user.email, "at": utcnow().isoformat(), "aggressive": bool(payload.acknowledged_aggressive) or bool(prev and prev.get("aggressive"))}
        elif prev:
            cfg_in["ack"] = prev  # editing other settings of an already-confirmed cluster keeps its confirmation
    try:
        after = save_config(db, cluster_id, cfg_in)
    except ConfigError as e:
        raise HTTPException(400, str(e))
    write_audit_event(
        db, event_category="settings", event_type="balance.auto_settings_changed", actor=user.email, actor_type="user",
        cluster_id=cluster_id, state_before=before, state_after=after,
    )
    return _cluster_view(db, cluster)


class PauseRequest(BaseModel):
    hours: float = 24


@router.post("/{cluster_id}/pause", dependencies=[Depends(require_admin)])
def pause_auto_balance(cluster_id: uuid.UUID, payload: PauseRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cluster = _cluster_or_404(db, cluster_id)
    if not 0 < payload.hours <= 24 * 30:
        raise HTTPException(400, "hours must be between 0 and 720")
    cfg = get_config(db, cluster_id)
    until = (utcnow() + timedelta(hours=payload.hours)).isoformat()
    save_config(db, cluster_id, {**cfg, "paused_until": until})
    write_audit_event(db, event_category="settings", event_type="balance.auto_paused", actor=user.email, actor_type="user",
                      cluster_id=cluster_id, state_after={"paused_until": until})
    return _cluster_view(db, cluster)


@router.post("/{cluster_id}/revoke", dependencies=[Depends(require_admin)])
def revoke_auto_approve(cluster_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """The off switch for auto-approve: back to Recommend only at once, the confirmation is dropped (turning it on again asks again),
    and an automatic move that was approved but has not started is cancelled. A migration already running finishes: there is no
    safe point to stop it halfway."""
    from datetime import datetime
    cluster = _cluster_or_404(db, cluster_id)
    before = get_config(db, cluster_id)
    if before["mode"] != "auto_approve":
        raise HTTPException(409, "auto-approve is not on for this cluster")
    after = save_config(db, cluster_id, {**before, "mode": "recommend"})
    state = get_state(db, cluster_id)
    save_state(db, cluster_id, {**state, "last_result": "revoked", "last_reason": f"auto-approve revoked by {user.email}", "last_checked_at": utcnow().isoformat()})
    stopped, running = [], []
    for o in db.query(Operation).filter(Operation.operation_type_id == "cluster.rebalance", Operation.status.in_(("approved", "revalidating", "evacuating", "executing", "monitoring", "verifying")), Operation.dismissed.is_(False)).all():
        ctx = o.context or {}
        if not (ctx.get("automatic") and ctx.get("auto_approved") and ctx.get("cluster_id") == str(cluster_id)):
            continue
        # The run checks this between moves, so a move that has not started yet never starts.
        o.context = {**ctx, "cancel_requested": True}
        db.commit()
        (stopped if o.status in ("approved", "revalidating") else running).append(str(o.id))
    write_audit_event(
        db, event_category="settings", event_type="balance.auto_revoked", actor=user.email, actor_type="user", cluster_id=cluster_id,
        state_before=before, state_after=after, metadata={"cancel_requested_for": stopped, "still_running": running},
    )
    dispatch_event(
        db, severity="warning", category="balance", recovered=False, observed_at=utcnow(),
        title=f"Auto-approve was revoked on {cluster.name} by {user.email}. Automatic balancing is back to Recommend only.",
    )
    view = _cluster_view(db, cluster)
    view["revoke_note"] = (
        "Auto-approve revoked. This cluster is back to Recommend only." +
        (" A move that was approved but had not started is cancelled." if stopped else "") +
        (" A migration that is already running will finish; it cannot be stopped halfway." if running else "")
    )
    return view


@router.delete("/{cluster_id}/pause", dependencies=[Depends(require_admin)])
def resume_auto_balance(cluster_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    cluster = _cluster_or_404(db, cluster_id)
    cfg = get_config(db, cluster_id)
    save_config(db, cluster_id, {**cfg, "paused_until": None})
    write_audit_event(db, event_category="settings", event_type="balance.auto_resumed", actor=user.email, actor_type="user", cluster_id=cluster_id)
    return _cluster_view(db, cluster)
