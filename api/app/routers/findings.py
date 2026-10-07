import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.findings import clear_triage, evaluate_findings, triage_state
from pyxie_core.models import Finding

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/findings", tags=["findings"], dependencies=[Depends(get_current_user)])

STATUSES = ("open", "acknowledged", "dismissed", "any")
PAST_TENSE = {"acknowledge": "acknowledged", "dismiss": "dismissed", "reopen": "reopened"}


def _iso(d):
    return d.isoformat() if d else None


@router.get("")
def list_findings(
    db: Session = Depends(get_db),
    active: bool | None = None,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
):
    """`status` is the operator triage state: open, acknowledged, dismissed or any. When omitted it is `open` for
    `active=true` (so every attention count and list in the app leaves out acknowledged and dismissed findings) and
    `any` otherwise."""
    if status is None:
        status = "open" if active is True else "any"
    if status not in STATUSES:
        raise HTTPException(422, "invalid status")
    q = db.query(Finding)
    if active is not None:
        q = q.filter(Finding.active.is_(active))
    if category:
        q = q.filter(Finding.category == category)
    if severity:
        q = q.filter(Finding.severity == severity)
    if status == "open":
        q = q.filter(Finding.acknowledged_at.is_(None), Finding.dismissed_at.is_(None))
    elif status == "acknowledged":
        q = q.filter(Finding.acknowledged_at.isnot(None), Finding.dismissed_at.is_(None))
    elif status == "dismissed":
        q = q.filter(Finding.dismissed_at.isnot(None))
    rows = q.order_by(Finding.last_observed.desc()).limit(500).all()
    return [
        {
            "id": str(r.id),
            "object_type": r.object_type,
            "object_id": str(r.object_id) if r.object_id else None,
            "category": r.category,
            "severity": r.severity,
            "title": r.title,
            "evidence": r.evidence,
            "first_observed": r.first_observed.isoformat(),
            "last_observed": r.last_observed.isoformat(),
            "active": r.active,
            "resolved_at": _iso(r.resolved_at),
            "confidence": r.confidence,
            "triage": triage_state(r),
            "acknowledged_at": _iso(r.acknowledged_at),
            "acknowledged_by": r.acknowledged_by,
            "dismissed_at": _iso(r.dismissed_at),
            "dismissed_by": r.dismissed_by,
        }
        for r in rows
    ]


class TriageRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    action: str  # acknowledge | dismiss | reopen


@router.post("/triage", dependencies=[Depends(require_admin)])
def triage_findings(payload: TriageRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Acknowledge, dismiss or reopen findings (one or many). Only active findings can be triaged: a resolved one has
    nothing left to mute, and a recurrence starts clean anyway."""
    if payload.action not in ("acknowledge", "dismiss", "reopen"):
        raise HTTPException(422, "invalid action")
    now = datetime.now(timezone.utc)
    rows = db.query(Finding).filter(Finding.id.in_(payload.ids)).all()
    changed, skipped = 0, 0
    for f in rows:
        if not f.active:
            skipped += 1
            continue
        before = triage_state(f)
        clear_triage(f)
        if payload.action == "acknowledge":
            f.acknowledged_at, f.acknowledged_by = now, user.email
        elif payload.action == "dismiss":
            f.dismissed_at, f.dismissed_by = now, user.email
        after = triage_state(f)
        if after == before:
            continue
        changed += 1
        write_audit_event(
            db,
            event_category="finding",
            event_type="finding." + PAST_TENSE[payload.action],
            actor=user.email,
            actor_type="user",
            state_before={"triage": before},
            state_after={"triage": after},
            metadata={"finding_id": str(f.id), "title": f.title, "severity": f.severity, "dedupe_key": f.dedupe_key},
            commit=False,
        )
    db.commit()
    return {"changed": changed, "skipped": skipped, "missing": len(payload.ids) - len(rows)}


@router.post("/evaluate", dependencies=[Depends(require_admin)])
def trigger_evaluate(db: Session = Depends(get_db)):
    return evaluate_findings(db)
