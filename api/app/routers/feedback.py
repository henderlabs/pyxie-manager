"""In-app feedback. The GitHub path is a prefilled link the browser opens (nothing is sent from here); the email path
sends through this instance's own SMTP. Either way PyXie only keeps a small local record. Diagnostics are a fixed,
secret-free list built here; the browser shows the exact text before anything leaves."""

import re
import uuid  # noqa: F401

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core import mail
from pyxie_core.audit import write_audit_event
from pyxie_core.models import AppSettings, FeedbackSubmission, Finding, Node, Workload
from pyxie_core.setup_status import build_steps, progress

from .. import config
from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .setup import _inputs

router = APIRouter(prefix="/api/feedback", tags=["feedback"], dependencies=[Depends(get_current_user)])

KINDS = ("bug", "feature", "feedback")
REPO = "henderlabs/pyxie-feedback"
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def _line(s: str) -> str:
    """One line only: no header injection through a title."""
    return " ".join((s or "").split())[:200]


def _settings(db: Session):
    return db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()


@router.get("")
def feedback_info(db: Session = Depends(get_db), user=Depends(get_current_user)):
    s = _settings(db)
    recipient = (s.feedback_email if s else None) or None
    recent = db.query(FeedbackSubmission).order_by(FeedbackSubmission.created_at.desc()).limit(20).all()
    return {
        "repo": REPO,
        "version": config.settings.APP_VERSION,
        "email_available": bool(recipient and s and s.smtp_enabled and s.smtp_host and s.smtp_from_address),
        "email_configured": bool(recipient),
        "feedback_email": recipient if user.is_admin else None,
        "smtp_enabled": bool(s and s.smtp_enabled),
        "recent": [
            {"id": str(r.id), "created_at": r.created_at.isoformat(), "kind": r.kind, "title": r.title,
             "channel": r.channel, "submitted_by": r.submitted_by, "diagnostics_included": r.diagnostics_included}
            for r in recent
        ],
    }


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)):
    """The complete, fixed list of what "Include diagnostics" adds on the server side. No hostnames, addresses, VM
    names, credentials or logs."""
    nodes = db.query(Node).filter(Node.is_missing.is_(False)).all()
    versions = sorted({n.pve_version for n in nodes if n.pve_version})
    guests = db.query(Workload).filter(Workload.is_missing.is_(False)).count()
    prog = progress(build_steps(_inputs(db)))
    open_f = db.query(Finding).filter(Finding.active.is_(True), Finding.acknowledged_at.is_(None), Finding.dismissed_at.is_(None))
    warn = open_f.filter(Finding.severity == "warning").count()
    crit = open_f.filter(Finding.severity == "critical").count()
    return {
        "fields": [
            {"label": "PyXie version", "value": config.settings.APP_VERSION},
            {"label": "Proxmox", "value": ", ".join(versions) if versions else "not connected"},
            {"label": "Size", "value": f"{len(nodes)} nodes, {guests} guests"},
            {"label": "Setup", "value": f"{prog['done']} of {prog['total']} steps done"},
            {"label": "Open findings", "value": f"{warn} warning, {crit} critical"},
        ]
    }


class Submission(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=200)
    channel: str  # github | copied
    diagnostics_included: bool = False


def _record(db: Session, user, kind: str, title: str, channel: str, diag: bool) -> None:
    db.add(FeedbackSubmission(kind=kind, title=_line(title), channel=channel, submitted_by=user.email, diagnostics_included=diag))
    write_audit_event(db, event_category="feedback", event_type=f"feedback.{channel}", actor=user.email, actor_type="user",
                      metadata={"kind": kind, "title": _line(title), "diagnostics_included": diag}, commit=False)
    db.commit()


@router.post("/submissions")
def record_submission(payload: Submission, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Remember that feedback was opened on GitHub / copied. Nothing is sent from here."""
    if payload.kind not in KINDS or payload.channel not in ("github", "copied"):
        raise HTTPException(422, "invalid kind or channel")
    _record(db, user, payload.kind, payload.title, payload.channel, payload.diagnostics_included)
    return {"ok": True}


class EmailRequest(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    diagnostics_text: str = Field(default="", max_length=4000)
    diagnostics_included: bool = False


@router.post("/email")
def send_by_email(payload: EmailRequest, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if payload.kind not in KINDS:
        raise HTTPException(422, "invalid kind")
    s = _settings(db)
    to = s.feedback_email if s else None
    if not (to and s.smtp_enabled):
        raise HTTPException(409, "Email feedback is not set up on this server.")
    title = _line(payload.title)
    body = (
        f"{payload.kind.upper()}: {title}\n"
        f"From: {user.email}\n"
        f"PyXie version: {config.settings.APP_VERSION}\n\n"
        f"{payload.description}\n"
        + (f"\n--- Diagnostics ---\n{payload.diagnostics_text}\n" if payload.diagnostics_included and payload.diagnostics_text else "")
    )
    try:
        mail.send_email(s, to, f"[PyXie {payload.kind}] {title}", body)
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "The email could not be sent. Check Settings > Email (SMTP), or use GitHub instead.")
    _record(db, user, payload.kind, title, "email", payload.diagnostics_included)
    return {"ok": True}


class FeedbackSettings(BaseModel):
    feedback_email: str | None = None


@router.put("/settings", dependencies=[Depends(require_admin)])
def update_settings(payload: FeedbackSettings, db: Session = Depends(get_db), user=Depends(get_current_user)):
    addr = (payload.feedback_email or "").strip() or None
    if addr and not EMAIL_RE.match(addr):
        raise HTTPException(422, "That does not look like an email address.")
    s = _settings(db)
    if s is None:
        raise HTTPException(409, "Settings are not initialised yet.")
    before = s.feedback_email
    s.feedback_email = addr
    write_audit_event(db, event_category="settings", event_type="settings.feedback_email_updated", actor=user.email, actor_type="user",
                      state_before={"feedback_email": before}, state_after={"feedback_email": addr}, commit=False)
    db.commit()
    return {"feedback_email": addr}
