import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pyxie_core.models import MetricPoint

from ..auth_deps import get_current_user
from ..deps import get_db

router = APIRouter(prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(get_current_user)])


@router.get("/{object_type}/{object_id}")
def get_metrics(
    object_type: str,
    object_id: uuid.UUID,
    db: Session = Depends(get_db),
    metric: str | None = None,
    window_days: int = 30,
):
    q = db.query(MetricPoint).filter(MetricPoint.object_type == object_type, MetricPoint.object_id == object_id)
    if metric:
        q = q.filter(MetricPoint.metric == metric)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    q = q.filter(MetricPoint.sampled_at >= cutoff)
    rows = q.order_by(MetricPoint.sampled_at).all()
    return [
        {"metric": r.metric, "sampled_at": r.sampled_at.isoformat(), "value": r.value}
        for r in rows
    ]
