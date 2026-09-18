import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.mail import send_email
from pyxie_core.models import AppSettings, NotificationRule
from pyxie_core.notifications import CATEGORIES, CATEGORY_KEYS, is_valid_email, resolve_recipients

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/notification-rules", tags=["notifications"], dependencies=[Depends(get_current_user)])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    categories: list[str] = []
    min_severity: Literal["warning", "critical"] = "critical"
    send_recovery: bool = True
    recipients: list[str] = []
    include_admins: bool = False


def _validate(payload: RuleIn) -> RuleIn:
    unknown = [c for c in payload.categories if c not in CATEGORY_KEYS]
    if unknown:
        raise HTTPException(422, f"unknown categories: {', '.join(unknown)}")
    bad = [a for a in payload.recipients if not is_valid_email(a.strip())]
    if bad:
        raise HTTPException(422, f"not valid email addresses: {', '.join(bad)}")
    if payload.enabled and not payload.recipients and not payload.include_admins:
        raise HTTPException(422, "an enabled rule needs at least one recipient (or 'all admins')")
    payload.name = payload.name.strip()
    payload.recipients = [a.strip() for a in payload.recipients]
    return payload


def _out(r: NotificationRule) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "enabled": r.enabled,
        "categories": r.categories or [],
        "min_severity": r.min_severity,
        "send_recovery": r.send_recovery,
        "recipients": r.recipients or [],
        "include_admins": r.include_admins,
    }


def _audit(db: Session, event_type: str, before, after) -> None:
    write_audit_event(
        db,
        event_category="settings",
        event_type=event_type,
        actor="user",
        actor_type="user",
        state_before=before,
        state_after=after,
    )


@router.get("/catalog")
def catalog():
    return {"categories": CATEGORIES, "severities": ["warning", "critical"]}


@router.get("")
def list_rules(db: Session = Depends(get_db)):
    return [_out(r) for r in db.query(NotificationRule).order_by(NotificationRule.created_at).all()]


@router.post("", dependencies=[Depends(require_admin)])
def create_rule(payload: RuleIn, db: Session = Depends(get_db)):
    payload = _validate(payload)
    rule = NotificationRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    _audit(db, "settings.notification_rule_created", None, _out(rule))
    return _out(rule)


def _get(db: Session, rule_id: uuid.UUID) -> NotificationRule:
    rule = db.query(NotificationRule).filter(NotificationRule.id == rule_id).one_or_none()
    if rule is None:
        raise HTTPException(404, "rule not found")
    return rule


@router.put("/{rule_id}", dependencies=[Depends(require_admin)])
def update_rule(rule_id: uuid.UUID, payload: RuleIn, db: Session = Depends(get_db)):
    payload = _validate(payload)
    rule = _get(db, rule_id)
    before = _out(rule)
    for field, value in payload.model_dump().items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    _audit(db, "settings.notification_rule_updated", before, _out(rule))
    return _out(rule)


@router.delete("/{rule_id}", dependencies=[Depends(require_admin)])
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db)):
    rule = _get(db, rule_id)
    before = _out(rule)
    db.delete(rule)
    db.commit()
    _audit(db, "settings.notification_rule_deleted", before, None)
    return {"status": "ok"}


@router.post("/{rule_id}/test", dependencies=[Depends(require_admin)])
def test_rule(rule_id: uuid.UUID, db: Session = Depends(get_db)):
    """Send a clearly-labelled test message to every recipient of this rule,
    regardless of whether the rule is enabled -- lets an operator verify
    addresses before switching it on."""
    rule = _get(db, rule_id)
    settings = db.query(AppSettings).filter(AppSettings.id == 1).one()
    if not settings.smtp_enabled:
        return {"status": "failed", "error": "email is not enabled -- configure the email server first"}
    recipients = resolve_recipients(db, rule)
    if not recipients:
        return {"status": "failed", "error": "this rule has no recipients"}
    sent, errors = [], []
    for address in recipients:
        try:
            send_email(
                settings,
                address,
                f"[PyXie] TEST: notification rule '{rule.name}'",
                f"This is a test of the PyXie notification rule '{rule.name}'.\n"
                f"Sent {datetime.now(timezone.utc).isoformat()}. No action is needed.\n",
            )
            sent.append(address)
        except Exception as e:  # noqa: BLE001 -- report per-address, don't 500
            errors.append(f"{address}: {e}")
    return {"status": "failed" if errors and not sent else "ok", "sent": sent, "errors": errors}
