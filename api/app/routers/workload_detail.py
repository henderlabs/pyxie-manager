"""Single-workload page API: DB overview plus live PVE reads.

Everything here is read-only. Live data comes through the inventory client; a
PVE failure is reported in the payload (`error`) instead of failing the request
so the page can still render what PyXie already knows.
"""
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Finding, Node, PveTarget, PveTask, Workload
from pyxie_core.workload_detail import (
    RRD_TIMEFRAMES,
    facts_from_config,
    guest_addresses,
    live_check,
    summarize_config,
    summarize_status,
    tasks_for_vmid,
    trim_rrd,
)

from ..auth_deps import get_current_user
from ..deps import get_db
from .. import schemas

router = APIRouter(prefix="/api/workloads", tags=["workload-detail"], dependencies=[Depends(get_current_user)])


def _load(db: Session, workload_id: uuid.UUID) -> tuple[Workload, Node, Cluster]:
    w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail="Workload not found")
    node = db.query(Node).filter(Node.id == w.node_id).one_or_none()
    cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one_or_none()
    if node is None or cluster is None:
        raise HTTPException(status_code=404, detail="Workload's host or cluster not found")
    return w, node, cluster


def _client(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
    if target is None:
        raise RuntimeError("no PVE target for this cluster")
    return build_pve_client(db, target)


@router.get("/{workload_id}/overview")
def workload_overview(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """What PyXie already stores: the row, its host/cluster names, active findings."""
    w, node, cluster = _load(db, workload_id)
    wid = str(w.id)
    findings = []
    for f in db.query(Finding).filter(Finding.active.is_(True)).all():
        evidence = f.evidence if isinstance(f.evidence, dict) else {}
        ids = evidence.get("workload_ids") or []
        if (f.object_type == "workload" and str(f.object_id) == wid) or wid in [str(i) for i in ids]:
            findings.append({"id": str(f.id), "severity": f.severity, "category": f.category, "title": f.title})
    return {
        "workload": schemas.WorkloadOut.model_validate(w).model_dump(mode="json"),
        "node": {"id": str(node.id), "name": node.name, "maintenance_mode": bool(node.maintenance_mode)},
        "cluster": {"id": str(cluster.id), "name": cluster.name},
        "findings": findings,
    }


@router.get("/{workload_id}/live")
def workload_live(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live status + config straight from PVE."""
    w, node, cluster = _load(db, workload_id)
    lxc = w.type == "lxc"
    out: dict = {"status": None, "config": None, "check": None, "error": None}
    try:
        client, _cred = _client(db, cluster)
        with client:
            status = config = None
            errors = []
            try:
                status = client.lxc_status_current(node.name, w.vmid) if lxc else client.qemu_status_current(node.name, w.vmid)
            except Exception as e:  # noqa: BLE001 -- surfaced to the page, not hidden
                errors.append(f"status: {e}")
            try:
                config = client.lxc_config(node.name, w.vmid) if lxc else client.qemu_config(node.name, w.vmid)
            except Exception as e:  # noqa: BLE001
                errors.append(f"config: {e}")
            out["status"] = summarize_status(status)
            out["config"] = summarize_config(config, lxc=lxc)
            out["check"] = live_check(status, w.status)
            out["error"] = "; ".join(errors) or None
    except Exception as e:  # noqa: BLE001
        out["check"] = live_check(None, w.status)
        out["error"] = str(e)
    return out


@router.get("/{workload_id}/rrd")
def workload_rrd(workload_id: uuid.UUID, timeframe: str = "hour", db: Session = Depends(get_db)):
    """PVE's own rrddata for this guest (the same series its Summary graphs use)."""
    if timeframe not in RRD_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe must be one of {', '.join(RRD_TIMEFRAMES)}")
    w, node, cluster = _load(db, workload_id)
    try:
        client, _cred = _client(db, cluster)
        with client:
            rows = (
                client.lxc_rrddata(node.name, w.vmid, timeframe)
                if w.type == "lxc"
                else client.qemu_rrddata(node.name, w.vmid, timeframe)
            )
    except Exception as e:  # noqa: BLE001
        return {"timeframe": timeframe, "rows": [], "error": str(e)}
    return {"timeframe": timeframe, "rows": trim_rrd(rows), "error": None}


@router.get("/{workload_id}/ips")
def workload_ips(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Guest-agent / container addresses. Separate and lazy: a guest whose agent is
    not answering costs a few seconds, which must not slow the rest of the page."""
    w, node, cluster = _load(db, workload_id)
    if w.status != "running":
        return {"addresses": [], "error": None}
    try:
        client, _cred = _client(db, cluster)
        with client:
            ifaces = client.lxc_interfaces(node.name, w.vmid) if w.type == "lxc" else client.qemu_agent_interfaces(node.name, w.vmid)
    except Exception as e:  # noqa: BLE001
        return {"addresses": [], "error": str(e)}
    out = guest_addresses(ifaces)
    return {"addresses": out, "error": None}


@router.get("/{workload_id}/tasks")
def workload_tasks(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    """Recent PVE tasks naming this guest, across every node it has lived on."""
    w, _node, _cluster = _load(db, workload_id)
    rows = (
        db.query(PveTask)
        .filter(PveTask.cluster_id == w.cluster_id, PveTask.upid.like(f"%:{w.vmid}:%"))
        .order_by(PveTask.started_at.desc().nullslast())
        .limit(500)
        .all()
    )
    return [
        {
            "id": str(t.id), "upid": t.upid, "task_type": t.task_type, "status": t.status, "exit_status": t.exit_status,
            "user": t.user, "node_id": str(t.node_id) if t.node_id else None,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "ended_at": t.ended_at.isoformat() if t.ended_at else None,
        }
        for t in tasks_for_vmid(rows, w.vmid)
    ]


_FACTS_TTL_SECONDS = 30
_facts_cache: dict = {"ts": 0.0, "data": None}
_facts_lock = threading.Lock()


def _build_facts(db: Session) -> dict:
    """Per-workload table facts: config-derived (Start at boot, agent, OS, disk) plus live
    uptime/lock from one cluster/resources call per cluster. About 1.5 s for 130 guests,
    which is why it is cached rather than read per request."""
    facts: dict[str, dict] = {}
    errors = 0
    workloads = db.query(Workload).filter(Workload.is_missing.is_(False)).all()
    nodes = {n.id: n for n in db.query(Node).all()}
    by_cluster: dict = {}
    for w in workloads:
        by_cluster.setdefault(w.cluster_id, []).append(w)
    for cluster_id, group in by_cluster.items():
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
        if cluster is None:
            continue
        try:
            client, _cred = _client(db, cluster)
        except Exception:  # noqa: BLE001
            errors += len(group)
            continue
        with client:
            try:
                resources = {r.get("vmid"): r for r in (client.cluster_resources("vm") or [])}
            except Exception:  # noqa: BLE001
                resources = {}
            for w in group:
                node = nodes.get(w.node_id)
                res = resources.get(w.vmid, {})
                entry = {
                    "uptime": res.get("uptime"), "lock": res.get("lock"), "template": bool(res.get("template")),
                    "start_at_boot": None, "agent_enabled": None, "ostype": None, "disk_bytes": None, "storages": [], "protection": None,
                }
                if node is not None:
                    lxc = w.type == "lxc"
                    try:
                        cfg = client.lxc_config(node.name, w.vmid) if lxc else client.qemu_config(node.name, w.vmid)
                        entry.update(facts_from_config(cfg, lxc=lxc) or {})
                    except Exception:  # noqa: BLE001
                        errors += 1
                facts[str(w.id)] = entry
    return {"facts": facts, "errors": errors, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/facts")
def workload_facts(db: Session = Depends(get_db)):
    """Bulk per-workload facts for the Workloads table columns, cached for 30 s."""
    with _facts_lock:
        if _facts_cache["data"] is not None and time.monotonic() - _facts_cache["ts"] < _FACTS_TTL_SECONDS:
            return _facts_cache["data"]
        data = _build_facts(db)
        _facts_cache.update(ts=time.monotonic(), data=data)
        return data
