"""In-app feedback. Both paths are links the user's own browser opens (a prefilled GitHub issue, or a mailto: to the
developers); nothing is sent from this server. PyXie only keeps a small local record. Diagnostics are a fixed,
secret-free list built here; the browser shows the exact text before anything leaves."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.models import FeedbackSubmission, Finding, Node, Workload
from pyxie_core.setup_status import build_steps, progress

from .. import config
from ..auth_deps import get_current_user
from ..deps import get_db
from .setup import _inputs

router = APIRouter(prefix="/api/feedback", tags=["feedback"], dependencies=[Depends(get_current_user)])

KINDS = ("bug", "feature", "feedback")
REPO = "henderlabs/pyxie-feedback"
# The developers' mailbox for the mailto: option. Empty hides the email button (set it once the mailbox exists).
FEEDBACK_EMAIL = ""


def _line(s: str) -> str:
    """One line only: no header injection through a title."""
    return " ".join((s or "").split())[:200]


@router.get("")
def feedback_info(db: Session = Depends(get_db)):
    recent = db.query(FeedbackSubmission).order_by(FeedbackSubmission.created_at.desc()).limit(20).all()
    return {
        "repo": REPO,
        "version": config.settings.APP_VERSION,
        "email_to": FEEDBACK_EMAIL or None,
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
    channel: str  # github | email | copied
    diagnostics_included: bool = False


def _record(db: Session, user, kind: str, title: str, channel: str, diag: bool) -> None:
    db.add(FeedbackSubmission(kind=kind, title=_line(title), channel=channel, submitted_by=user.email, diagnostics_included=diag))
    write_audit_event(db, event_category="feedback", event_type=f"feedback.{channel}", actor=user.email, actor_type="user",
                      metadata={"kind": kind, "title": _line(title), "diagnostics_included": diag}, commit=False)
    db.commit()


@router.post("/submissions")
def record_submission(payload: Submission, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Remember that feedback was opened on GitHub / in the mail app / copied. Nothing is sent from here."""
    if payload.kind not in KINDS or payload.channel not in ("github", "email", "copied"):
        raise HTTPException(422, "invalid kind or channel")
    _record(db, user, payload.kind, payload.title, payload.channel, payload.diagnostics_included)
    return {"ok": True}
