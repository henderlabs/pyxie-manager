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

from .mem_pressure import memory_pressure_verdict, pressure_summaries, pressure_summary_one
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


# A guest cannot use more than 100% of its own RAM. PVE's VM memory series falls
# back to the host-side size of the QEMU process (allocation plus overhead, so
# ~100.4%) whenever guest stats are unavailable; those samples are not usage and
# used to drive the "peak memory already exceeds the target" suggestions.
MEM_PCT_VALID_MAX = 100.0


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
    # VMs with no guest memory stats are never assessed for memory, so show their real
    # (host-side, possibly >100%) numbers exactly as PVE reports them; the >100% filter
    # only protects the sizing math for VMs that DO have guest stats.
    mem_stats = observation_stats(
        db, "workload", workload.id, "mem_pct", since=since,
        max_value=None if workload.mem_guest_stats is False else MEM_PCT_VALID_MAX,
    )
    pressure = pressure_summary_one(db, workload.id, since=since)
    return _compute_assessment(workload, cpu_stats, mem_stats, cpu_target_pct, mem_target_pct, round_vcpu_even, pressure=pressure)


def _compute_assessment(
    workload: Workload, cpu_stats: dict, mem_stats: dict, cpu_target_pct: int, mem_target_pct: int,
    round_vcpu_even: bool = False, pressure: dict | None = None,
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
        # PVE gives no guest memory stats for this VM, so its "memory usage"
        # is the host-side figure (~100% of allocation), not what the guest
        # uses. Not assessed -- otherwise every such VM got an "increase
        # memory / at risk of OOM" suggestion.
        "memory_host_only": workload.mem_guest_stats is False,
        # Why there is no "add memory" suggestion despite high usage ("gathering
        # memory-pressure data (3 of 7 days)", "high usage is file cache ...").
        "memory_note": None,
        "memory_pressure": pressure,
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

    if workload.memory_bytes and mem_stats["p95"] is not None and workload.mem_guest_stats is not False:
        headroom_p95_bytes = mem_stats["p95"] / 100 * workload.memory_bytes * 1.3
        # Size to the 99th percentile, not the single highest sample: one stray
        # reading (a ballooned guest briefly reporting its host-side size, a
        # backup window) used to decide the whole recommendation. P99 over months
        # of one-minute samples still covers every sustained peak; it only
        # ignores the top 1%. Falls back to the absolute max if no p99 was computed.
        peak_pct = mem_stats["p99"] if mem_stats.get("p99") is not None else (mem_stats["max"] or 0)
        headroom_max_bytes = peak_pct / 100 * workload.memory_bytes * _peak_multiplier(mem_target_pct)
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
            # PVE's "used" memory counts file cache, so high usage alone does not mean the
            # guest needs more RAM. For VMs, require evidence of real pressure (sustained
            # swapping); containers have no such counters and keep the usage-only rule.
            gate = memory_pressure_verdict(pressure) if workload.type == "vm" else {"state": "pressure", "detail": None}
            if gate["state"] == "pressure":
                why = f"; {gate['detail']}" if gate.get("detail") else ""
                result["memory_suggestion"] = {
                    "current_bytes": workload.memory_bytes,
                    "suggested_bytes": suggested_bytes,
                    "direction": "increase",
                    "reason": (
                        f"99th-percentile memory usage {peak_pct}% of allocation over {round(days)}d "
                        f"(single highest sample {mem_stats['max']}%) "
                        f"already exceeds the {mem_target_pct}% target and there is real memory pressure"
                        f"{why} -- at risk of swapping/OOM"
                    ),
                }
            else:
                result["memory_note"] = gate["note"]

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
    mem_batch = observation_stats_batch(db, "workload", no_cutoff_ids, "mem_pct", max_value=MEM_PCT_VALID_MAX, with_p99=True)

    pressure_by_id = pressure_summaries(db)
    host_only_ids = [wl.id for wl in workloads if wl.mem_guest_stats is False and wl.id in set(no_cutoff_ids)]
    host_only_mem = observation_stats_batch(db, "workload", host_only_ids, "mem_pct") if host_only_ids else {}

    results = []
    for wl in workloads:
        since = since_by_id.get(wl.id)
        if since is None:
            cpu_stats = cpu_batch[wl.id]
            mem_stats = host_only_mem.get(wl.id) or mem_batch[wl.id]
            pressure = pressure_by_id.get(wl.id)
        else:
            cpu_stats = observation_stats(db, "workload", wl.id, "cpu_pct", since=since)
            mem_stats = observation_stats(
                db, "workload", wl.id, "mem_pct", since=since,
                max_value=None if wl.mem_guest_stats is False else MEM_PCT_VALID_MAX,
            )
            pressure = pressure_summary_one(db, wl.id, since=since)
        results.append(_compute_assessment(wl, cpu_stats, mem_stats, cpu_target_pct, mem_target_pct, round_vcpu_even, pressure=pressure))
    return results


def _serialize_assessment(a: dict) -> dict:
    """JSON-ready shape -- str() on the UUID fields, everything else
    already plain. Shared by refresh_rightsizing_cache() (what gets
    stored) and the live-recompute response, so cached data is exactly
    what GET /rightsizing would have returned computing it live."""
    return {
        "workload_id": str(a["workload_id"]),
        "vmid": a["vmid"],
        "node_id": str(a["node_id"]),
        "name": a["name"],
        "current_vcpu": a["current_vcpu"],
        "current_memory_bytes": a["current_memory_bytes"],
        "cpu": a["cpu"],
        "memory": a["memory"],
        "observation_days": a["observation_days"],
        "confidence": a["confidence"],
        "currently_running": a["currently_running"],
        "status": a["status"],
        "cpu_suggestion": a["cpu_suggestion"],
        "memory_suggestion": a["memory_suggestion"],
        "memory_host_only": a.get("memory_host_only", False),
        "memory_note": a.get("memory_note"),
        "memory_pressure": a.get("memory_pressure"),
    }


def refresh_rightsizing_cache(db, assessments: list[dict] | None = None) -> list[dict]:
    """Computes (unless already computed by the caller -- generate_recommendations()
    needs assess_all_workloads() for its own rightsizing-recommendation pass
    anyway, and used to run it a SECOND time here; now it computes once and
    passes the result in) and persists to the rightsizing_cache singleton
    row, returning the same JSON-ready list GET /rightsizing serves.
    Called by the worker's regular run_all cycle and by the admin-only
    manual POST /rightsizing/recompute trigger."""
    from datetime import datetime, timezone

    from .models import RightsizingCache

    if assessments is None:
        assessments = assess_all_workloads(db)
    serialized = [_serialize_assessment(a) for a in assessments]

    row = db.query(RightsizingCache).filter(RightsizingCache.id == 1).one_or_none()
    if row is None:
        row = RightsizingCache(id=1)
        db.add(row)
    row.computed_at = datetime.now(timezone.utc)
    row.assessments = serialized
    db.commit()
    return serialized
