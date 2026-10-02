"""Guest memory-pressure evidence for Rightsizing.

PVE's per-VM "memory used" is total minus the guest's FREE memory, so file cache
counts as used and a perfectly healthy cache-heavy VM (MongoDB, Kubernetes nodes)
reads 95-98%. PVE does not expose "available" memory. It does expose how much the
guest has SWAPPED IN, and a guest that is genuinely short of RAM swaps in
continuously while a merely-full one does not (measured 2026-10-02: 0 of the 21
VMs above 90% were swapping). This module samples those counters and turns them
into the one question Rightsizing needs answered before suggesting more memory:
is there actual memory pressure, not enough history yet, or none?
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import MemPressureSample, Node, Workload

COLLECT_EVERY_SECONDS = 240   # the full sync runs about every 300s; sample on each run
RETENTION_DAYS = 90
LOOKBACK_DAYS = 14            # how much history Rightsizing weighs
MIN_DAYS = 7                  # history needed before a "no pressure" verdict is trusted
SWAP_IN_BYTES_PER_DAY = 256 * 1024 * 1024   # sustained swap-in above this = real shortage
FETCH_THREADS = 8


def collect_memory_pressure(db: Session, client, cluster_id) -> int:
    """Read-only: one status/current GET per running VM (only VMs reporting
    balloon stats produce a row). Throttled to ~once per 5 minutes per cluster.
    Returns rows written."""
    now = datetime.now(timezone.utc)
    newest = (
        db.query(func.max(MemPressureSample.sampled_at))
        .join(Workload, Workload.id == MemPressureSample.workload_id)
        .filter(Workload.cluster_id == cluster_id)
        .scalar()
    )
    if newest is not None and (now - newest).total_seconds() < COLLECT_EVERY_SECONDS:
        return 0

    vms = (
        db.query(Workload.id, Workload.vmid, Node.name)
        .join(Node, Node.id == Workload.node_id)
        .filter(
            Workload.cluster_id == cluster_id, Workload.type == "vm",
            Workload.status == "running", Workload.is_missing.is_(False),
        )
        .all()
    )

    def one(v):
        try:
            status = client.qemu_status_current(v.name, v.vmid)
        except Exception:
            return None
        bi = (status or {}).get("ballooninfo") or {}
        if not bi.get("total_mem"):
            return None
        return {
            "workload_id": v.id, "sampled_at": now,
            "swapped_in_bytes": int(bi.get("mem_swapped_in") or 0),
            "swapped_out_bytes": int(bi.get("mem_swapped_out") or 0),
            "major_faults": int(bi.get("major_page_faults") or 0),
            "free_bytes": int(bi["free_mem"]) if bi.get("free_mem") is not None else None,
            "total_bytes": int(bi["total_mem"]),
        }

    with ThreadPoolExecutor(FETCH_THREADS) as pool:
        rows = [r for r in pool.map(one, vms) if r]
    if rows:
        stmt = pg_insert(MemPressureSample).values(rows).on_conflict_do_nothing(
            index_elements=["workload_id", "sampled_at"]
        )
        db.execute(stmt)
    return len(rows)


def prune_memory_pressure(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return db.query(MemPressureSample).filter(MemPressureSample.sampled_at < cutoff).delete()


_SUMMARY_SQL = """
WITH s AS (
  SELECT workload_id, sampled_at, swapped_in_bytes AS si, swapped_out_bytes AS so,
         LAG(swapped_in_bytes)  OVER w AS psi,
         LAG(swapped_out_bytes) OVER w AS pso
  FROM workload_mem_pressure
  WHERE sampled_at >= :cutoff {only}
  WINDOW w AS (PARTITION BY workload_id ORDER BY sampled_at)
)
SELECT workload_id,
       MIN(sampled_at) AS first_at, MAX(sampled_at) AS last_at, COUNT(*) AS samples,
       -- counters are cumulative since guest boot: only count increases (a drop is a guest reboot)
       COALESCE(SUM(CASE WHEN psi IS NOT NULL AND si >= psi THEN si - psi END), 0) AS swap_in_bytes,
       COALESCE(SUM(CASE WHEN pso IS NOT NULL AND so >= pso THEN so - pso END), 0) AS swap_out_bytes
FROM s GROUP BY workload_id
"""


def _row_to_summary(r) -> dict:
    days = max(0.0, (r.last_at - r.first_at).total_seconds() / 86400)
    return {
        "days": round(days, 2), "samples": int(r.samples),
        "swap_in_bytes": int(r.swap_in_bytes), "swap_out_bytes": int(r.swap_out_bytes),
    }


def pressure_summaries(db: Session, since=None) -> dict:
    """workload_id -> summary over the lookback window (or from `since` if later)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    if since is not None and since > cutoff:
        cutoff = since
    rows = db.execute(text(_SUMMARY_SQL.format(only="")), {"cutoff": cutoff}).all()
    return {r.workload_id: _row_to_summary(r) for r in rows}


def pressure_summary_one(db: Session, workload_id, since=None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    if since is not None and since > cutoff:
        cutoff = since
    row = db.execute(
        text(_SUMMARY_SQL.format(only="AND workload_id = :wid")), {"cutoff": cutoff, "wid": workload_id}
    ).first()
    return _row_to_summary(row) if row else None


def _gib(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GiB"


def memory_pressure_verdict(summary) -> dict:
    """Pure function. state is 'gathering' (not enough history to say),
    'no_pressure' (high usage is file cache), or 'pressure' (guest is genuinely
    short of memory)."""
    days = summary["days"] if summary else 0.0
    if days < MIN_DAYS:
        return {
            "state": "gathering",
            "note": f"gathering memory-pressure data ({days:.0f} of {MIN_DAYS} days)",
            "detail": None,
        }
    per_day = summary["swap_in_bytes"] / max(days, 1.0)
    if per_day >= SWAP_IN_BYTES_PER_DAY:
        return {
            "state": "pressure", "note": None,
            "detail": f"the guest swapped in {_gib(summary['swap_in_bytes'])} over {days:.0f}d ({_gib(per_day)}/day)",
        }
    return {
        "state": "no_pressure",
        "note": f"high usage is file cache -- no memory pressure in {days:.0f}d of readings",
        "detail": None,
    }
