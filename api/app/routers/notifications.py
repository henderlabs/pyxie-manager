import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.models import Notification

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_notifications(db: Session = Depends(get_db), status: str | None = None):
    q = db.query(Notification)
    if status:
        q = q.filter(Notification.status == status)
    rows = q.order_by(Notification.created_at.desc()).limit(200).all()
    return [
        {
            "id": str(n.id),
            "severity": n.severity,
            "title": n.title,
            "message": n.message,
            "status": n.status,
            "source": n.source,
            "created_at": n.created_at.isoformat(),
        }
        for n in rows
    ]


class StatusUpdate(BaseModel):
    status: str


@router.post("/{notification_id}/status", dependencies=[Depends(require_admin)])
def update_status(notification_id: uuid.UUID, payload: StatusUpdate, db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id).one_or_none()
    if n is None:
        raise HTTPException(404, "notification not found")
    if payload.status not in ("unread", "read", "dismissed"):
        raise HTTPException(422, "invalid status")
    n.status = payload.status
    db.commit()
    return {"status": "ok"}
