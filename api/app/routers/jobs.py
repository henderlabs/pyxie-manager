from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pyxie_core.models import InternalJobRun

from ..auth_deps import get_current_user
from ..deps import get_db

router = APIRouter(prefix="/api/internal-jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_internal_jobs(db: Session = Depends(get_db), limit: int = 100):
    rows = db.query(InternalJobRun).order_by(InternalJobRun.started_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": str(r.id),
            "job_name": r.job_name,
            "started_at": r.started_at.isoformat(),
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "status": r.status,
            "result_summary": r.result_summary,
            "error": r.error,
        }
        for r in rows
    ]
