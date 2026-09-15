import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pyxie_core.models import AuditEvent

from ..auth_deps import get_current_user
from ..deps import get_db
from .. import schemas

router = APIRouter(prefix="/api", tags=["audit"], dependencies=[Depends(get_current_user)])


@router.get("/audit-events", response_model=list[schemas.AuditEventOut])
def list_audit_events(
    db: Session = Depends(get_db),
    event_type: str | None = None,
    event_category: str | None = None,
    result: str | None = None,
    actor: str | None = None,
    site_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    node_id: uuid.UUID | None = None,
    workload_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 100,
):
    q = db.query(AuditEvent)
    if event_type:
        q = q.filter(AuditEvent.event_type == event_type)
    if event_category:
        q = q.filter(AuditEvent.event_category == event_category)
    if result:
        q = q.filter(AuditEvent.result == result)
    if actor:
        q = q.filter(AuditEvent.actor == actor)
    if site_id:
        q = q.filter(AuditEvent.site_id == site_id)
    if cluster_id:
        q = q.filter(AuditEvent.cluster_id == cluster_id)
    if node_id:
        q = q.filter(AuditEvent.node_id == node_id)
    if workload_id:
        q = q.filter(AuditEvent.workload_id == workload_id)
    if since:
        q = q.filter(AuditEvent.timestamp >= since)
    return q.order_by(AuditEvent.timestamp.desc()).limit(min(limit, 500)).all()
