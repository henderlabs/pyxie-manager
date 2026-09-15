import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.maintenance import generate_plan
from pyxie_core.models import MaintenancePlan, MaintenancePlanWorkload, Node

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"], dependencies=[Depends(get_current_user)])


def _serialize_plan(db: Session, plan: MaintenancePlan) -> dict:
    node = db.query(Node).filter(Node.id == plan.node_id).one()
    is_stale = node.last_seen is not None and plan.inventory_state_at is not None and node.last_seen > plan.inventory_state_at
    workload_rows = db.query(MaintenancePlanWorkload).filter(MaintenancePlanWorkload.plan_id == plan.id).all()
    return {
        "id": str(plan.id),
        "node_id": str(plan.node_id),
        "node_name": node.name,
        "generated_at": plan.generated_at.isoformat(),
        "inventory_state_at": plan.inventory_state_at.isoformat() if plan.inventory_state_at else None,
        "protection_state_at": plan.protection_state_at.isoformat() if plan.protection_state_at else None,
        "metric_state_at": plan.metric_state_at.isoformat() if plan.metric_state_at else None,
        "status": "stale" if is_stale else "current",
        "summary": plan.summary,
        "blocking_safety_rules": plan.blocking_safety_rules or [],
        "workloads": [
            {
                "workload_id": str(w.workload_id),
                "classification": w.classification,
                "reasons": w.reasons,
                "blocking_safety_rules": w.blocking_safety_rules,
            }
            for w in workload_rows
        ],
    }


@router.post("/plans/{node_id}/generate", dependencies=[Depends(require_admin)])
def create_plan(node_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    plan = generate_plan(db, node, actor=user.email)
    write_audit_event(
        db,
        event_category="maintenance",
        event_type="maintenance_plan.generated",
        actor=user.email,
        actor_type="user",
        node_id=node.id,
        metadata={"plan_id": str(plan.id), "status_label": plan.summary.get("status_label")},
    )
    return _serialize_plan(db, plan)


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    plans = db.query(MaintenancePlan).order_by(MaintenancePlan.generated_at.desc()).limit(50).all()
    return [_serialize_plan(db, p) for p in plans]


@router.get("/plans/{plan_id}")
def get_plan(plan_id: uuid.UUID, db: Session = Depends(get_db)):
    plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).one_or_none()
    if plan is None:
        raise HTTPException(404, "plan not found")
    return _serialize_plan(db, plan)
