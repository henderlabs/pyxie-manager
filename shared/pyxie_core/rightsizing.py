"""CPU/RAM rightsizing analysis. Two-directional: suggests a DECREASE when a
workload is comfortably over-allocated, and an INCREASE when its observed
usage already runs the new-allocation math the other way -- i.e. it's
already too tight for the same target this module uses to judge a
decrease. Never emits an actionable suggestion below 'moderate' confidence
(>=30 days observed), per the spec's cold-start handling.

Sizing is based on the WORSE of two safety margins, not P95 alone: P95 *
1.5 (CPU) / 1.3 (memory) handles steady workloads fine, but silently
starves anything that's idle most of the time and spikes hard when
actually used (a monitoring box that's quiet except during its own poll
cycle, a backup job, a lab VM that "sits idle until called upon") --  P95
never sees the spike if it's brief enough, even though the workload
clearly needs that much when it happens. The second margin catches that
case by sizing so observed MAX lands at roughly cpu_target_pct/mem_target_pct
of the new allocation, rather than the old fixed 1.1x -- which left
observed peak at ~91% of the new size with zero room for measurement noise
or growth -- a real gap found live, this is what left a real VM pegged at
100% CPU after a downsize. Those targets are configurable in
Settings (AppSettings.rightsizing_{cpu,mem}_peak_target_pct), not fixed
constants, and the same target is what the increase side checks against:
if a workload's observed peak/P95 ALREADY exceeds it on the CURRENT
allocation, that's the mirror-image finding -- it's under-, not
over-provisioned. A dead zone (suggested vs current within ~5-15%,
resource-dependent) keeps borderline workloads from flapping between the
two every cycle. Windows guests also get an absolute floor regardless of
usage -- observed behavior, not a policy guess: a Windows guest choked
down to 1 vCPU can become unresponsive to the point of needing a hard
reset even while "usage" reads low, because a lot of its overhead is
bookkeeping/idle housekeeping that doesn't show up as CPU% the way a Linux
workload's would.

Memory suggestions round to the nearest whole GB (no reason to keep finer
granularity). vCPU suggestions are exact by default -- rounding up to an
even number is available via AppSettings.rightsizing_round_vcpu_even
(off by default: single-socket hardware doesn't have the usual
NUMA-locality reason to avoid odd core counts, and forcing round-ups
wastes allocation on smaller hosts).
"""

from sqlalchemy import func

from .metrics import confidence_for_observation_days, observation_days, observation_stats, observation_stats_batch
from .models import AppSettings, Operation, Workload

MIN_CONFIDENCE_FOR_ACTION = {"moderate", "high"}

WINDOWS_MIN_VCPU = 2
WINDOWS_MIN_MEMORY_BYTES = 4 * 1024 * 1024 * 1024

DEFAULT_PEAK_TARGET_PCT = 75


def _is_windows(workload: Workload) -> bool:
    # PVE's ostype enum: win7/win8/win10/win11/w2k*/wxp/wvista all start
    # with 'w'; every non-Windows value (l24, l26, solaris, other) doesn't.
    return bool(workload.os_type and workload.os_type.startswith("w"))


def _json_safe_stats(stats: dict) -> dict:
    """observation_stats() returns real datetime objects for earliest/latest
    (needed for observation_days() math) -- convert to isoformat strings
    before this dict is ever embedded in a JSONB column."""
    out = dict(stats)
    out["earliest"] = stats["earliest"].isoformat() if stats["earliest"] else None
    out["latest"] = stats["latest"].isoformat() if stats["latest"] else None
    return out


def _last_resize_completed_at(db, workload_id):
    op = (
        db.query(Operation)
        .filter(
            Operation.workload_id == workload_id,
            Operation.operation_type_id == "workload.resize",
            Operation.status == "completed",
        )
        .order_by(Operation.completed_at.desc())
        .first()
    )
    return op.completed_at if op else None


def _last_resize_completed_at_batch(db, workload_ids: list) -> dict:
    """Batched version of _last_resize_completed_at() -- one grouped query
    for every workload_id instead of one query per workload. MAX() is
    associative, so this is mathematically identical to calling the
    single-workload version once per id, just without the round trips."""
    if not workload_ids:
        return {}
    rows = (
        db.query(Operation.workload_id, func.max(Operation.completed_at))
        .filter(
            Operation.workload_id.in_(workload_ids),
            Operation.operation_type_id == "workload.resize",
            Operation.status == "completed",
        )
        .group_by(Operation.workload_id)
        .all()
    )
    return {wid: completed_at for wid, completed_at in rows if completed_at is not None}


def _peak_multiplier(target_pct: float) -> float:
    """Size so observed peak lands at target_pct% of the new allocation --
    e.g. a 75% target means the peak multiplier is 1/0.75 = 1.333x."""
    return 100.0 / target_pct


def _rightsizing_settings(db) -> tuple[int, int, bool]:
    settings = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
    if settings is None:
        return DEFAULT_PEAK_TARGET_PCT, DEFAULT_PEAK_TARGET_PCT, False
    return (
        settings.rightsizing_cpu_peak_target_pct,
        settings.rightsizing_mem_peak_target_pct,
        settings.rightsizing_round_vcpu_even,
    )


def assess_workload(
    db, workload: Workload, cpu_target_pct: int, mem_target_pct: int, round_vcpu_even: bool = False
) -> dict:
    """Single-workload path -- still does its own 3 queries (last-resize
    lookup + 2x observation_stats), used directly wherever just one
    workload's assessment is needed. assess_all_workloads() below does NOT
    call this in a loop anymore (that was the real N+1: 130 workloads x ~5
    queries each = 650+ round trips, confirmed live as a 4+ second cost on
    cre-pyxie's real fleet size) -- it batches the same three lookups
    across every workload instead, then calls _compute_assessment() with
    the results, which is the exact same math this function runs below,
    shared so the batched path can't silently drift from this one."""
    since = _last_resize_completed_at(db, workload.id)
    cpu_stats = observation_stats(db, "workload", workload.id, "cpu_pct", since=since)
    mem_stats = observation_stats(db, "workload", workload.id, "mem_pct", since=since)
    return _compute_assessment(workload, cpu_stats, mem_stats, cpu_target_pct, mem_target_pct, round_vcpu_even)


def _compute_assessment(
    workload: Workload, cpu_stats: dict, mem_stats: dict, cpu_target_pct: int, mem_target_pct: int,
    round_vcpu_even: bool = False,
) -> dict:
    days = observation_days(
        min([d for d in (cpu_stats["earliest"], mem_stats["earliest"]) if d], default=None),
        max([d for d in (cpu_stats["latest"], mem_stats["latest"]) if d], default=None),
    )
    confidence = confidence_for_observation_days(days)

    result = {
        "workload_id": workload.id,
        "cluster_id": workload.cluster_id,
        "node_id": workload.node_id,
        "vmid": workload.vmid,
        "name": workload.name,
        "current_vcpu": workload.cpu_cores,
        "current_memory_bytes": workload.memory_bytes,
        "cpu": _json_safe_stats(cpu_stats),
        "memory": _json_safe_stats(mem_stats),
        "observation_days": round(days, 1),
        "confidence": confidence,
        # The cpu/memory stats above are historical (whatever was observed
        # while this workload was last running -- possibly months ago for
        # something that's been off a while), never "as of
        # right now" for a workload that isn't running right now. Without
        # this flag the table has no way to tell a genuinely-current
        # reading apart from stale history, which is exactly what made it
        # look inconsistent with the Workloads table's live meters --
        # they're not the same statistic to begin with, but a stopped
        # workload's numbers here were silently ungated history with
        # nothing marking them as such.
        "currently_running": workload.status == "running",
        "status": workload.status,
        "cpu_suggestion": None,
        "memory_suggestion": None,
    }

    if confidence not in MIN_CONFIDENCE_FOR_ACTION:
        return result

    # A powered-off workload's recent usage is 0% by definition -- that's
    # not "idle," it's "off," and would otherwise skew P95/max toward a
    # false "barely uses any of its allocation" suggestion. Only ever
    # suggest a change for a workload that's actually running right now.
    if workload.status != "running":
        return result

    is_windows = _is_windows(workload)
    min_vcpu = WINDOWS_MIN_VCPU if is_windows else 1
    min_memory_bytes = WINDOWS_MIN_MEMORY_BYTES if is_windows else 512 * 1024 * 1024

    if workload.cpu_cores and cpu_stats["p95"] is not None:
        headroom_p95 = cpu_stats["p95"] / 100 * workload.cpu_cores * 1.5
        headroom_max = (cpu_stats["max"] or 0) / 100 * workload.cpu_cores * _peak_multiplier(cpu_target_pct)
        suggested = max(min_vcpu, round(max(headroom_p95, headroom_max)))
        if round_vcpu_even and suggested % 2 != 0:
            # Round up, never down -- rounding down would eat into the
            # safety margin just computed above.
            suggested += 1

        if suggested < workload.cpu_cores:
            reason = f"P95 CPU usage {cpu_stats['p95']}% of {workload.cpu_cores} vCPU over {round(days)}d (peak {cpu_stats['max']}%)"
            if is_windows and suggested == min_vcpu and round(max(headroom_p95, headroom_max)) < min_vcpu:
                reason += f" -- floored at {min_vcpu} vCPU, Windows guest"
            result["cpu_suggestion"] = {
                "current": workload.cpu_cores,
                "suggested": suggested,
                "direction": "decrease",
                "reason": reason,
            }
        elif suggested > workload.cpu_cores:
            result["cpu_suggestion"] = {
                "current": workload.cpu_cores,
                "suggested": suggested,
                "direction": "increase",
                "reason": (
                    f"Peak CPU usage {cpu_stats['max']}% of {workload.cpu_cores} vCPU over {round(days)}d "
                    f"already exceeds the {cpu_target_pct}% target -- at risk of scheduling contention"
                ),
            }

    if workload.memory_bytes and mem_stats["p95"] is not None:
        headroom_p95_bytes = mem_stats["p95"] / 100 * workload.memory_bytes * 1.3
        headroom_max_bytes = (mem_stats["max"] or 0) / 100 * workload.memory_bytes * _peak_multiplier(mem_target_pct)
        headroom_needed_bytes = max(headroom_p95_bytes, headroom_max_bytes, min_memory_bytes)
        # round suggestion to nearest whole GB (half-GB steps like
        # 8.5GB/9.5GB were needless precision), never below the
        # applicable floor
        one_gb = 1024 * 1024 * 1024
        suggested_bytes = max(min_memory_bytes, round(headroom_needed_bytes / one_gb) * one_gb)

        if suggested_bytes < workload.memory_bytes * 0.85:
            reason = f"P95 memory usage {mem_stats['p95']}% of allocation over {round(days)}d (peak {mem_stats['max']}%)"
            if is_windows and suggested_bytes == min_memory_bytes:
                reason += f" -- floored at {min_memory_bytes // (1024**3)}GB, Windows guest"
            result["memory_suggestion"] = {
                "current_bytes": workload.memory_bytes,
                "suggested_bytes": suggested_bytes,
                "direction": "decrease",
                "reason": reason,
            }
        elif suggested_bytes > workload.memory_bytes * 1.05:
            result["memory_suggestion"] = {
                "current_bytes": workload.memory_bytes,
                "suggested_bytes": suggested_bytes,
                "direction": "increase",
                "reason": (
                    f"Peak memory usage {mem_stats['max']}% of allocation over {round(days)}d "
                    f"already exceeds the {mem_target_pct}% target -- at risk of swapping/OOM"
                ),
            }

    return result


def assess_all_workloads(db) -> list[dict]:
    """Batched: one grouped query for every workload's last-resize cutoff,
    then one pair of batched observation_stats queries (cpu_pct, mem_pct)
    covering every workload with no cutoff -- the overwhelming majority,
    since a resize is a rare event, not something most workloads have ever
    had. Only the few workloads that HAVE been resized (a real per-workload
    `since` date, which a single grouped query can't apply per-row) fall
    back to the single-workload observation_stats() path. Whichever path a
    workload's stats came from, _compute_assessment() runs the identical
    suggestion math assess_workload() runs for the single-workload case --
    verified to produce byte-identical output against the old
    one-query-per-workload implementation."""
    cpu_target_pct, mem_target_pct, round_vcpu_even = _rightsizing_settings(db)
    workloads = db.query(Workload).filter(Workload.is_missing.is_(False)).all()
    if not workloads:
        return []

    since_by_id = _last_resize_completed_at_batch(db, [wl.id for wl in workloads])
    no_cutoff_ids = [wl.id for wl in workloads if wl.id not in since_by_id]
    cpu_batch = observation_stats_batch(db, "workload", no_cutoff_ids, "cpu_pct")
    mem_batch = observation_stats_batch(db, "workload", no_cutoff_ids, "mem_pct")

    results = []
    for wl in workloads:
        since = since_by_id.get(wl.id)
        if since is None:
            cpu_stats = cpu_batch[wl.id]
            mem_stats = mem_batch[wl.id]
        else:
            cpu_stats = observation_stats(db, "workload", wl.id, "cpu_pct", since=since)
            mem_stats = observation_stats(db, "workload", wl.id, "mem_pct", since=since)
        results.append(_compute_assessment(wl, cpu_stats, mem_stats, cpu_target_pct, mem_target_pct, round_vcpu_even))
    return results
