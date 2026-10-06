"""Background QEMU-responsiveness watch.

Every ~60 s the worker asks PVE for each running VM's live status (one timed call each, about
7 s for 116 VMs when healthy), folds the answer into `workload_liveness`, and findings raise
"VM is not responding" after two bad probes in a row. See pyxie_core.vm_liveness for the
assessment and streak logic; this module is only the database and PVE plumbing.
"""

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .discovery import build_pve_client
from .models import Cluster, PveTarget, Workload, WorkloadLiveness
from .vm_liveness import FRESH_SECONDS, next_liveness_row, scan_vm_liveness, suppress_node_wide_failures

INTERVAL_SECONDS = 60


def _row_dict(r: WorkloadLiveness) -> dict:
    return {
        "state": r.state, "detail": r.detail, "elapsed": r.elapsed, "checked_at": r.checked_at,
        "bad_since": r.bad_since, "consecutive_bad": r.consecutive_bad,
    }


def refresh_liveness(db: Session) -> dict:
    """Probe every running VM once and store the results. Read-only against PVE."""
    now = datetime.now(timezone.utc)
    probed = bad = 0
    for target in db.query(PveTarget).all():
        try:
            client, _cred = build_pve_client(db, target)
        except Exception:
            continue
        with client:
            try:
                resources = client.cluster_resources("vm") or []
            except Exception:
                continue
            running = [
                (r["node"], int(r["vmid"]))
                for r in resources
                if r.get("type") == "qemu" and r.get("status") == "running" and not r.get("template")
                and r.get("node") and r.get("vmid") is not None
            ]
            results = scan_vm_liveness(client, running)
        results = suppress_node_wide_failures(results, {vmid: node for node, vmid in running})
        for cluster in db.query(Cluster).filter(Cluster.pve_target_id == target.id).all():
            ids = {
                vmid: wid
                for vmid, wid in db.query(Workload.vmid, Workload.id).filter(
                    Workload.cluster_id == cluster.id, Workload.is_missing.is_(False), Workload.type == "vm"
                )
            }
            prev = {
                r.workload_id: _row_dict(r)
                for r in db.query(WorkloadLiveness).filter(WorkloadLiveness.workload_id.in_(list(ids.values())))
            } if ids else {}
            rows = []
            for vmid, assessment in results.items():
                wid = ids.get(vmid)
                if wid is None:
                    continue
                row = next_liveness_row(prev.get(wid), assessment, now)
                row["workload_id"] = wid
                rows.append(row)
                probed += 1
                bad += 1 if row["consecutive_bad"] else 0
            if rows:
                stmt = pg_insert(WorkloadLiveness).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["workload_id"],
                    set_={c: getattr(stmt.excluded, c) for c in ("state", "detail", "elapsed", "checked_at", "bad_since", "consecutive_bad")},
                )
                db.execute(stmt)
    db.commit()
    return {"probed": probed, "bad": bad}


def fresh_liveness(db: Session, now: datetime | None = None) -> dict[str, dict]:
    """{workload_id: {state, detail, elapsed, since}} for results newer than FRESH_SECONDS."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, dict] = {}
    for r in db.query(WorkloadLiveness).all():
        checked = r.checked_at if r.checked_at.tzinfo else r.checked_at.replace(tzinfo=timezone.utc)
        if (now - checked).total_seconds() > FRESH_SECONDS:
            continue
        out[str(r.workload_id)] = {
            "state": r.state, "detail": r.detail, "elapsed": r.elapsed or 0.0,
            "since": r.bad_since.isoformat() if r.bad_since else None,
        }
    return out
