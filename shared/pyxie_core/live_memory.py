"""Near-live per-VM memory for the RAM meter.

One cheap PVE call per cluster (cluster resources, the same list the Datacenter
table and the VM summary screen use) every ~30 seconds, upserted into
workload_live_mem. Independent of the heavy 5-minute sync.

For VMs that report guest memory stats, PVE's figure alternates between the real
guest-used value and the host-side size of the QEMU process whenever the balloon
stats briefly drop out (PAW-JT-JohnLee: 24% then 100.4%, every few seconds). A
reading equal to the host-side size is that fallback, not usage, so it is skipped
and the previous good reading stays. VMs with no guest stats at all, and VMs whose
readings are genuinely full, are shown exactly as PVE reports them.
"""

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .discovery import build_pve_client
from .models import Cluster, PveTarget, Workload, WorkloadLiveMem

INTERVAL_SECONDS = 30
FRESH_SECONDS = 180  # readers ignore rows older than this and fall back
HOST_TOLERANCE = 0.005
HOST_TOLERANCE_FLOOR_BYTES = 8 * 1024 * 1024


def is_host_fallback(mem, maxmem, guest_stats, host_bytes) -> bool:
    """True when this reading is PVE's host-side fallback for a VM that does report
    guest stats (so it should not replace the last good reading)."""
    if guest_stats is not True or not host_bytes or not maxmem:
        return False
    return mem >= host_bytes - max(HOST_TOLERANCE * maxmem, HOST_TOLERANCE_FLOOR_BYTES)


def live_memory_rows(resource_rows: list, info_by_vmid: dict, now) -> list[dict]:
    """Pure: PVE resource entries -> rows to upsert. `info_by_vmid` maps vmid to
    {id, guest_stats, host_bytes}. Running guests with a memory figure and a known
    vmid only; host-side fallback readings are skipped."""
    out = []
    for r in resource_rows or []:
        if r.get("status") != "running" or r.get("mem") is None or r.get("vmid") is None:
            continue
        info = info_by_vmid.get(int(r["vmid"]))
        if info is None:
            continue
        if is_host_fallback(r["mem"], r.get("maxmem"), info.get("guest_stats"), info.get("host_bytes")):
            continue
        out.append({"workload_id": info["id"], "mem_used_bytes": int(r["mem"]), "sampled_at": now})
    return out


def refresh_live_memory(db: Session) -> int:
    """Returns rows upserted. Read-only against PVE."""
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
            info = {
                w.vmid: {"id": w.id, "guest_stats": w.mem_guest_stats, "host_bytes": w.mem_host_bytes}
                for w in db.query(Workload.vmid, Workload.id, Workload.mem_guest_stats, Workload.mem_host_bytes).filter(
                    Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)
                ).all()
            }
            rows = live_memory_rows(resources, info, now)
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
