"""Per-host page API: live PVE node status, storage and rrd graphs. Read-only.

PVE failures are reported in the payload (`error`) instead of failing the request
so the page can still render what PyXie already stores.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Node, PveTarget
from pyxie_core.node_detail import summarize_node_status, summarize_storage, trim_node_rrd
from pyxie_core.workload_detail import RRD_TIMEFRAMES

from ..auth_deps import get_current_user
from ..deps import get_db

router = APIRouter(prefix="/api/nodes", tags=["node-detail"], dependencies=[Depends(get_current_user)])


def _load(db: Session, node_id: uuid.UUID) -> tuple[Node, Cluster]:
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one_or_none() if node else None
    if node is None or cluster is None:
        raise HTTPException(status_code=404, detail="Host not found")
    return node, cluster


def _client(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
    if target is None:
        raise RuntimeError("no PVE target for this cluster")
    return build_pve_client(db, target)


@router.get("/{node_id}/live")
def node_live(node_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live node status and its storage, straight from PVE."""
    node, cluster = _load(db, node_id)
    out: dict = {"status": None, "storage": [], "error": None}
    errors: list[str] = []
    try:
        client, _cred = _client(db, cluster)
        with client:
            try:
                out["status"] = summarize_node_status(client.node_status(node.name))
            except Exception as e:  # noqa: BLE001 -- surfaced to the page
                errors.append(f"status: {e}")
            try:
                out["storage"] = summarize_storage(client.storage_list(node.name))
            except Exception as e:  # noqa: BLE001
                errors.append(f"storage: {e}")
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    out["error"] = "; ".join(errors) or None
    return out


@router.get("/{node_id}/rrd")
def node_rrd(node_id: uuid.UUID, timeframe: str = "hour", db: Session = Depends(get_db)):
    """PVE's own node rrddata (the series its Summary graphs use)."""
    if timeframe not in RRD_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe must be one of {', '.join(RRD_TIMEFRAMES)}")
    node, cluster = _load(db, node_id)
    try:
        client, _cred = _client(db, cluster)
        with client:
            rows = client.node_rrddata(node.name, timeframe)
    except Exception as e:  # noqa: BLE001
        return {"timeframe": timeframe, "rows": [], "error": str(e)}
    return {"timeframe": timeframe, "rows": trim_node_rrd(rows), "error": None}
