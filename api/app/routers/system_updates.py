"""Settings > Updates: release status, the update request, and live progress. Admin only.

The app never touches Docker. It reads what the host updater (ops/docker/updater.py, run from cron)
writes into the update folder and writes a single request file that the updater re-validates.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.models import Operation
from pyxie_core.updates import (
    PRE_EXECUTION_STATUSES, TERMINAL_STATUSES, block_reasons, read_history, read_json, rollback_target,
    tail_lines, validate_apply, write_request,
)

from .. import config
from ..auth_deps import require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/system", tags=["system-updates"])

UPDATE_DIR = Path(os.environ.get("PYXIE_UPDATE_DIR", "/update"))


def _in_flight(db: Session) -> int:
    return db.query(Operation).filter(~Operation.status.in_(TERMINAL_STATUSES + PRE_EXECUTION_STATUSES)).count()


def _request(db: Session, user, payload: dict, event: str) -> dict:
    try:
        write_request(UPDATE_DIR, {**payload, "requested_by": user.email, "requested_at": datetime.now(timezone.utc).isoformat()})
    except FileExistsError:
        raise HTTPException(status_code=409, detail="A request is already waiting for the updater.")
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"Could not reach the updater: {e}")
    write_audit_event(db, event_category="system", event_type=event, actor=user.email, actor_type="user", metadata=payload)
    return {"status": "requested"}


def _pending_request() -> dict | None:
    for name in ("request.json", "request.processing"):
        r = read_json(UPDATE_DIR / name, None)
        if isinstance(r, dict):
            return {k: r.get(k) for k in ("action", "version", "requested_by", "requested_at")}
    return None


@router.get("/updates")
def update_status(db: Session = Depends(get_db), user=Depends(require_admin)):
    installed = UPDATE_DIR.is_dir() and (UPDATE_DIR / "status.json").exists()
    status = read_json(UPDATE_DIR / "status.json", {})
    state = read_json(UPDATE_DIR / "state.json", {})
    history = read_history(UPDATE_DIR / "history.jsonl")
    in_flight = _in_flight(db)
    current = config.settings.APP_VERSION
    waiting = (UPDATE_DIR / "request.json").exists() or (UPDATE_DIR / "request.processing").exists()
    return {
        "current": current,
        "installed": installed,
        "status": status,
        "state": state,
        "history": history,
        "log": tail_lines(UPDATE_DIR / "update.log", 80),
        "in_flight_operations": in_flight,
        "blocked_by": block_reasons(installed=installed, state=state, in_flight=in_flight, request_waiting=waiting),
        "rollback": rollback_target(history, current),
        "pending": _pending_request(),
    }


@router.post("/updates/check")
def check_now(db: Session = Depends(get_db), user=Depends(require_admin)):
    return _request(db, user, {"action": "check"}, "system.update.check_requested")


class ApplyBody(BaseModel):
    version: str


@router.post("/updates/apply")
def apply_update(body: ApplyBody, db: Session = Depends(get_db), user=Depends(require_admin)):
    status = read_json(UPDATE_DIR / "status.json", {})
    state = read_json(UPDATE_DIR / "state.json", {})
    waiting = (UPDATE_DIR / "request.json").exists() or (UPDATE_DIR / "request.processing").exists()
    reasons = block_reasons(installed=UPDATE_DIR.is_dir(), state=state, in_flight=_in_flight(db), request_waiting=waiting)
    if reasons:
        raise HTTPException(status_code=409, detail=" ".join(reasons))
    try:
        version = validate_apply(body.version, status, config.settings.APP_VERSION)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _request(db, user, {"action": "update", "version": version}, "system.update.requested")


@router.post("/updates/rollback")
def rollback(db: Session = Depends(get_db), user=Depends(require_admin)):
    state = read_json(UPDATE_DIR / "state.json", {})
    waiting = (UPDATE_DIR / "request.json").exists() or (UPDATE_DIR / "request.processing").exists()
    reasons = block_reasons(installed=UPDATE_DIR.is_dir(), state=state, in_flight=_in_flight(db), request_waiting=waiting)
    if reasons:
        raise HTTPException(status_code=409, detail=" ".join(reasons))
    if not rollback_target(read_history(UPDATE_DIR / "history.jsonl"), config.settings.APP_VERSION):
        raise HTTPException(status_code=422, detail="There is no update to undo.")
    return _request(db, user, {"action": "rollback"}, "system.update.rollback_requested")
