import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Any

from pyxie_core.audit import write_audit_event
from pyxie_core.models import Policy

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/policies", tags=["policies"], dependencies=[Depends(get_current_user)])


def _serialize(p: Policy) -> dict:
    return {
        "id": str(p.id),
        "scope_type": p.scope_type,
        "scope_id": str(p.scope_id) if p.scope_id else None,
        "key": p.key,
        "value": p.value,
        "updated_at": p.updated_at.isoformat(),
    }


@router.get("")
def list_policies(db: Session = Depends(get_db), scope_type: str | None = None):
    q = db.query(Policy)
    if scope_type:
        q = q.filter(Policy.scope_type == scope_type)
    return [_serialize(p) for p in q.order_by(Policy.key).all()]


class PolicySet(BaseModel):
    scope_type: str = "organization"
    scope_id: uuid.UUID | None = None
    key: str
    value: Any


@router.put("", dependencies=[Depends(require_admin)])
def set_policy(payload: PolicySet, db: Session = Depends(get_db), user=Depends(get_current_user)):
    existing = (
        db.query(Policy)
        .filter(Policy.scope_type == payload.scope_type, Policy.scope_id == payload.scope_id, Policy.key == payload.key)
        .one_or_none()
    )
    before = existing.value if existing else None
    if existing is None:
        existing = Policy(scope_type=payload.scope_type, scope_id=payload.scope_id, key=payload.key, value=payload.value)
        db.add(existing)
    else:
        existing.value = payload.value
    db.commit()
    db.refresh(existing)

    write_audit_event(
        db,
        event_category="settings",
        event_type="policy.changed",
        actor=user.email,
        actor_type="user",
        state_before={"value": before},
        state_after={"value": payload.value},
        metadata={"key": payload.key, "scope_type": payload.scope_type},
    )
    return _serialize(existing)


@router.delete("/{policy_id}", dependencies=[Depends(require_admin)])
def delete_policy(policy_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    p = db.query(Policy).filter(Policy.id == policy_id).one_or_none()
    if p is None:
        raise HTTPException(404, "policy not found")
    db.delete(p)
    db.commit()
    write_audit_event(db, event_category="settings", event_type="policy.deleted", actor=user.email, actor_type="user")
    return {"status": "ok"}
