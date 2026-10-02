"""Near-live per-VM memory for the RAM meter.

One cheap PVE call per cluster (cluster resources, the same list the Datacenter
table and the VM summary screen use) every ~30 seconds, upserted into
workload_live_mem. Independent of the heavy 5-minute sync.
"""

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .discovery import build_pve_client
from .models import Cluster, PveTarget, Workload, WorkloadLiveMem

INTERVAL_SECONDS = 30
FRESH_SECONDS = 180  # readers ignore rows older than this and fall back


def live_memory_rows(resource_rows: list, workload_id_by_vmid: dict, now) -> list[dict]:
    """Pure: PVE resource entries -> rows to upsert. Running guests with a memory
    figure and a known vmid only."""
    out = []
    for r in resource_rows or []:
        if r.get("status") != "running" or r.get("mem") is None or r.get("vmid") is None:
            continue
        wid = workload_id_by_vmid.get(int(r["vmid"]))
        if wid is None:
            continue
        out.append({"workload_id": wid, "mem_used_bytes": int(r["mem"]), "sampled_at": now})
    return out


def refresh_live_memory(db: Session) -> int:
    """Returns rows upserted. Read-only against PVE; raises nothing the caller
    must handle beyond logging."""
    now = datetime.now(timezone.utc)
    written = 0
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
        for cluster in db.query(Cluster).filter(Cluster.pve_target_id == target.id).all():
            ids = {
                w.vmid: w.id
                for w in db.query(Workload.vmid, Workload.id).filter(
                    Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)
                ).all()
            }
            rows = live_memory_rows(resources, ids, now)
            if not rows:
                continue
            stmt = pg_insert(WorkloadLiveMem).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["workload_id"],
                set_={"mem_used_bytes": stmt.excluded.mem_used_bytes, "sampled_at": stmt.excluded.sampled_at},
            )
            db.execute(stmt)
            written += len(rows)
    db.commit()
    return written
