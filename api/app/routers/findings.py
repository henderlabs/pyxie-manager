import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pyxie_core.findings import evaluate_findings
from pyxie_core.models import Finding

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/findings", tags=["findings"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_findings(
    db: Session = Depends(get_db),
    active: bool | None = None,
    category: str | None = None,
    severity: str | None = None,
):
    q = db.query(Finding)
    if active is not None:
        q = q.filter(Finding.active.is_(active))
    if category:
        q = q.filter(Finding.category == category)
    if severity:
        q = q.filter(Finding.severity == severity)
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
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            "confidence": r.confidence,
        }
        for r in rows
    ]


@router.post("/evaluate", dependencies=[Depends(require_admin)])
def trigger_evaluate(db: Session = Depends(get_db)):
    return evaluate_findings(db)
