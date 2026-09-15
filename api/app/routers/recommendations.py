import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.capacity import compute_capacity
from pyxie_core.models import Recommendation, Workload
from pyxie_core.recommendations import _placement_recommendations, generate_recommendations
from pyxie_core.rightsizing import assess_all_workloads

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api", tags=["recommendations"], dependencies=[Depends(get_current_user)])


def _serialize(r: Recommendation) -> dict:
    return {
        "id": str(r.id),
        "object_type": r.object_type,
        "object_id": str(r.object_id) if r.object_id else None,
        "category": r.category,
        "title": r.title,
        "evidence": r.evidence,
        "expected_benefit": r.expected_benefit,
        "possible_impact": r.possible_impact,
        "severity": r.severity,
        "risk": r.risk,
        "confidence": r.confidence,
        "observation_window_days": r.observation_window_days,
        "generated_at": r.generated_at.isoformat(),
        "lifecycle_state": r.lifecycle_state,
        "snoozed_until": r.snoozed_until.isoformat() if r.snoozed_until else None,
    }


@router.get("/recommendations")
def list_recommendations(
    db: Session = Depends(get_db),
    category: str | None = None,
    lifecycle_state: str | None = None,
):
    q = db.query(Recommendation)
    if category:
        q = q.filter(Recommendation.category == category)
    if lifecycle_state:
        q = q.filter(Recommendation.lifecycle_state == lifecycle_state)
    else:
        q = q.filter(Recommendation.lifecycle_state != "resolved")
    rows = q.order_by(Recommendation.generated_at.desc()).limit(500).all()
    return [_serialize(r) for r in rows]


@router.post("/recommendations/evaluate", dependencies=[Depends(require_admin)])
def trigger_recommendations(db: Session = Depends(get_db)):
    return generate_recommendations(db)


class BalanceLoadPlanRequest(BaseModel):
    node_ids: list[uuid.UUID] | None = None


@router.post("/recommendations/balance-load-plan", dependencies=[Depends(require_admin)])
def balance_load_plan(payload: BalanceLoadPlanRequest, db: Session = Depends(get_db)):
    """On-demand, live re-run of the same load-balance engine behind the
    Recommendations page's placement category -- the Maintenance page's
    "Balance Load" trigger. node_ids, when given, restricts which
    workloads are evaluated as move candidates to those currently on the
    given node(s); destinations are never restricted to that set. Purely
    read-only -- creates nothing. Each returned item still has to go
    through the existing /vm-migrations/dry-run -> approve path
    individually, same Safety Contract as every other migration; this
    endpoint only proposes."""
    source_node_ids = set(payload.node_ids) if payload.node_ids else None
    recs = _placement_recommendations(db, source_node_ids=source_node_ids)
    out = []
    for r in recs:
        wl = db.query(Workload).filter(Workload.id == r["object_id"]).one_or_none()
        if wl is None:
            continue
        ev = r["evidence"]
        out.append(
            {
                "workload_id": str(wl.id),
                "workload_name": wl.name or f"vmid {wl.vmid}",
                "vmid": wl.vmid,
                "current_node": ev["current_node"],
                "suggested_node": ev["suggested_node"],
                "suggested_node_id": ev["suggested_node_id"],
                "improvement": ev["improvement"],
                "reasons": ev["reasons"],
                "suggested_storage": ev.get("suggested_storage"),
            }
        )
    return out


class LifecycleUpdate(BaseModel):
    lifecycle_state: str
    snooze_days: int | None = None


@router.post("/recommendations/{rec_id}/lifecycle", dependencies=[Depends(require_admin)])
def update_lifecycle(rec_id: uuid.UUID, payload: LifecycleUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    rec = db.query(Recommendation).filter(Recommendation.id == rec_id).one_or_none()
    if rec is None:
        raise HTTPException(404, "recommendation not found")
    if payload.lifecycle_state not in ("open", "acknowledged", "snoozed", "dismissed", "resolved"):
        raise HTTPException(422, "invalid lifecycle_state")

    before = rec.lifecycle_state
    rec.lifecycle_state = payload.lifecycle_state
    if payload.lifecycle_state == "snoozed":
        rec.snoozed_until = datetime.now(timezone.utc) + timedelta(days=payload.snooze_days or 7)
    if payload.lifecycle_state == "resolved":
        rec.resolved_at = datetime.now(timezone.utc)
    db.commit()

    write_audit_event(
        db,
        event_category="recommendation",
        event_type="recommendation.lifecycle_changed",
        actor=user.email,
        actor_type="user",
        state_before={"lifecycle_state": before},
        state_after={"lifecycle_state": payload.lifecycle_state},
    )
    return _serialize(rec)


@router.get("/rightsizing")
def rightsizing(db: Session = Depends(get_db)):
    assessments = assess_all_workloads(db)
    out = []
    for a in assessments:
        out.append(
            {
                "workload_id": str(a["workload_id"]),
                "vmid": a["vmid"],
                "node_id": str(a["node_id"]),
                "name": a["name"],
                "current_vcpu": a["current_vcpu"],
                "current_memory_bytes": a["current_memory_bytes"],
                "cpu": a["cpu"],
                "memory": a["memory"],
                "observation_days": a["observation_days"],
                "confidence": a["confidence"],
                "currently_running": a["currently_running"],
                "status": a["status"],
                "cpu_suggestion": a["cpu_suggestion"],
                "memory_suggestion": a["memory_suggestion"],
            }
        )
    return out


@router.get("/capacity")
def capacity(db: Session = Depends(get_db)):
    return compute_capacity(db)
