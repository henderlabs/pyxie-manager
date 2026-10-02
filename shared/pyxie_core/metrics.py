"""Historical metrics collection, sourced from PVE's own RRD data (no
Prometheus dependency required). PVE keeps a small, fixed number of RRD
points (~70) per timeframe regardless of span, so pulling 'month' and 'year'
on first collection backfills real history immediately without us having to
wait weeks for our own samples to accumulate -- this directly improves the
rightsizing cold-start experience described in the spec.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import MetricPoint, Node, Workload
from .pve_client import PveClient

NODE_METRICS = ("cpu_pct", "mem_pct", "mem_used_bytes")
WORKLOAD_METRICS = ("cpu_pct", "mem_pct", "mem_used_bytes")

BACKFILL_TIMEFRAMES = ("year", "month")
REFRESH_TIMEFRAME = "hour"


def _upsert_points(db: Session, rows: list[dict]):
    if not rows:
        return
    stmt = pg_insert(MetricPoint).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["object_type", "object_id", "metric", "sampled_at"]
    )
    db.execute(stmt)


def _node_rows(node_id, rrd_points: list[dict]) -> list[dict]:
    rows = []
    for p in rrd_points:
        if "time" not in p:
            continue
        ts = datetime.fromtimestamp(p["time"], tz=timezone.utc)
        cpu = p.get("cpu")
        memused = p.get("memused")
        memtotal = p.get("memtotal")
        if cpu is not None:
            rows.append({"object_type": "node", "object_id": node_id, "metric": "cpu_pct", "sampled_at": ts, "value": cpu * 100})
        if memused is not None and memtotal:
            rows.append({"object_type": "node", "object_id": node_id, "metric": "mem_pct", "sampled_at": ts, "value": memused / memtotal * 100})
        if memused is not None:
            rows.append({"object_type": "node", "object_id": node_id, "metric": "mem_used_bytes", "sampled_at": ts, "value": memused})
    return rows


# A VM "has guest memory stats" when at least one sample in the window differs
# from the host-side figure by more than this fraction of its allocation (floor
# 8 MiB). With no balloon stats PVE reports mem == memhost in every sample.
MEM_GUEST_STATS_TOLERANCE = 0.005
MEM_GUEST_STATS_FLOOR_BYTES = 8 * 1024 * 1024


def mem_guest_stats_from_rrd(rrd_points: list[dict]):
    """True / False / None (not enough comparable samples to say)."""
    comparable = False
    for p in rrd_points:
        mem, host, maxmem = p.get("mem"), p.get("memhost"), p.get("maxmem")
        if mem is None or not host or not maxmem:
            continue
        comparable = True
        if abs(host - mem) > max(MEM_GUEST_STATS_TOLERANCE * maxmem, MEM_GUEST_STATS_FLOOR_BYTES):
            return True
    return False if comparable else None


def _workload_rows(workload_id, rrd_points: list[dict]) -> list[dict]:
    rows = []
    for p in rrd_points:
        if "time" not in p:
            continue
        ts = datetime.fromtimestamp(p["time"], tz=timezone.utc)
        cpu = p.get("cpu")
        mem = p.get("mem")
        maxmem = p.get("maxmem")
        if cpu is not None:
            rows.append({"object_type": "workload", "object_id": workload_id, "metric": "cpu_pct", "sampled_at": ts, "value": cpu * 100})
        if mem is not None and maxmem:
            rows.append({"object_type": "workload", "object_id": workload_id, "metric": "mem_pct", "sampled_at": ts, "value": mem / maxmem * 100})
        if mem is not None:
            rows.append({"object_type": "workload", "object_id": workload_id, "metric": "mem_used_bytes", "sampled_at": ts, "value": mem})
    return rows


def collect_metrics_for_cluster(db: Session, client: PveClient, cluster_id) -> dict:
    """Pull RRD data for every node/workload currently in this cluster.

    Uses 'hour' (finest resolution) on every call; additionally backfills
    'month'+'year' the first time an object has no metric_points at all, so
    a brand-new deployment gets real history on day one instead of starting
    from a blank slate.
    """
    collected = {"nodes": 0, "workloads": 0, "points": 0}

    nodes = db.query(Node).filter(Node.cluster_id == cluster_id, Node.is_missing.is_(False)).all()
    for node in nodes:
        has_history = db.query(MetricPoint.object_id).filter(
            MetricPoint.object_type == "node", MetricPoint.object_id == node.id
        ).first() is not None

        timeframes = [REFRESH_TIMEFRAME] if has_history else [*BACKFILL_TIMEFRAMES, REFRESH_TIMEFRAME]
        rows: list[dict] = []
        for tf in timeframes:
            try:
                data = client.node_rrddata(node.name, tf)
            except Exception:
                continue
            rows.extend(_node_rows(node.id, data))
        _upsert_points(db, rows)
        collected["nodes"] += 1
        collected["points"] += len(rows)

    workloads = db.query(Workload).filter(Workload.cluster_id == cluster_id, Workload.is_missing.is_(False)).all()
    for wl in workloads:
        node = db.query(Node).filter(Node.id == wl.node_id).one_or_none()
        if node is None:
            continue
        has_history = db.query(MetricPoint.object_id).filter(
            MetricPoint.object_type == "workload", MetricPoint.object_id == wl.id
        ).first() is not None

        timeframes = [REFRESH_TIMEFRAME] if has_history else [*BACKFILL_TIMEFRAMES, REFRESH_TIMEFRAME]
        rrd_fn = client.qemu_rrddata if wl.type == "vm" else client.lxc_rrddata
        rows = []
        hour_data = None
        for tf in timeframes:
            try:
                data = rrd_fn(node.name, wl.vmid, tf)
            except Exception:
                continue
            if tf == REFRESH_TIMEFRAME:
                hour_data = data
            rows.extend(_workload_rows(wl.id, data))
        _upsert_points(db, rows)
        if wl.type == "vm" and hour_data:
            flag = mem_guest_stats_from_rrd(hour_data)
            if flag is not None and wl.mem_guest_stats != flag:
                wl.mem_guest_stats = flag
        collected["workloads"] += 1
        collected["points"] += len(rows)

    return collected


def observation_stats(
    db: Session, object_type: str, object_id, metric: str,
    window_days: int | None = None, since=None, max_value: float | None = None,
):
    """Returns (avg, p95, max, sample_count, earliest, latest) for a metric,
    optionally restricted to the last `window_days` days and/or to samples
    at or after an absolute `since` timestamp. `since` matters specifically
    for rightsizing after a resize: a percentage sample is only meaningful
    relative to whatever allocation was active when it was recorded, so a
    95th-percentile computed across a resize boundary silently mixes
    "P95 of the OLD allocation" with "P95 of the NEW one" as if they were
    the same baseline -- since always excludes pre-resize samples for that
    reason, not just as a data-freshness nicety.
    """
    q = db.query(
        func.avg(MetricPoint.value),
        func.max(MetricPoint.value),
        func.count(MetricPoint.value),
        func.min(MetricPoint.sampled_at),
        func.max(MetricPoint.sampled_at),
    ).filter(
        MetricPoint.object_type == object_type,
        MetricPoint.object_id == object_id,
        MetricPoint.metric == metric,
        MetricPoint.value.isnot(None),
    )
    if max_value is not None:
        q = q.filter(MetricPoint.value <= max_value)
    if window_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        q = q.filter(MetricPoint.sampled_at >= cutoff)
    if since:
        q = q.filter(MetricPoint.sampled_at >= since)
    avg, mx, count, earliest, latest = q.one()

    p95 = None
    p99 = None
    if count:
        pq = db.query(MetricPoint.value).filter(
            MetricPoint.object_type == object_type,
            MetricPoint.object_id == object_id,
            MetricPoint.metric == metric,
            MetricPoint.value.isnot(None),
        )
        if max_value is not None:
            pq = pq.filter(MetricPoint.value <= max_value)
        if window_days:
            pq = pq.filter(MetricPoint.sampled_at >= cutoff)
        if since:
            pq = pq.filter(MetricPoint.sampled_at >= since)
        values = [v[0] for v in pq.order_by(MetricPoint.value).all()]
        if values:
            idx = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
            p95 = values[idx]
            idx99 = min(len(values) - 1, int(round(0.99 * (len(values) - 1))))
            p99 = values[idx99]

    return {
        "avg": round(avg, 1) if avg is not None else None,
        "p95": round(p95, 1) if p95 is not None else None,
        "p99": round(p99, 1) if p99 is not None else None,
        "max": round(mx, 1) if mx is not None else None,
        "sample_count": count or 0,
        "earliest": earliest,
        "latest": latest,
    }


def observation_stats_batch(
    db: Session, object_type: str, object_ids: list, metric: str, max_value: float | None = None,
    with_p99: bool = False,
) -> dict:
    """Batched version of observation_stats() for the common case (no
    per-object `since`/`window_days` cutoff) -- rightsizing's real bottleneck
    was calling observation_stats() once per workload (2 queries each, one of
    which -- the p95 one -- pulls back every raw sample), so 130 workloads
    meant 260+ sequential round trips (measured live: 4.4s on cre-pyxie's
    real fleet). The obvious fix -- fetch every raw value for every workload
    in one query, group/sort/index in Python -- was tried and measured
    WORSE (10.7s): transferring ~300K+ raw rows through SQLAlchemy's ORM
    (UUID/float deserialization per row) dominates regardless of how few
    queries that takes, and Postgres sorting one huge combined multi-workload
    result set by an unindexed column is itself expensive.

    What actually works: the grouped aggregate (avg/max/count/earliest/
    latest) in one query -- fast, ~130 tiny output rows, no raw-row
    transfer, and avg/max/count are associative so this is mathematically
    identical to computing them one workload at a time. For p95
    specifically, compute each workload's target RANK from its own count
    (the exact same 0-indexed round(0.95*(n-1)) formula the single-workload
    path uses -- computed in Python, so there's no risk of a SQL round()
    tie-breaking rule silently disagreeing with Python's), then ask
    Postgres for exactly the one value at that rank per workload
    (`ORDER BY value OFFSET rank LIMIT 1`) -- Postgres does the sort
    server-side and only the single selected value comes back, never the
    raw samples. Measured: 130 of these OFFSET/LIMIT queries in ~390ms,
    vs 4.5s+ to bulk-transfer the same data.

    A caller that needs a `since`/`window_days` cutoff (rightsizing after a
    resize, capacity.py's 14-day window) still uses observation_stats()
    per-object -- rare enough (a resized workload) or already narrow enough
    in scope not to be the bottleneck this exists for."""
    if not object_ids:
        return {}

    empty = {"avg": None, "p95": None, "p99": None, "max": None, "sample_count": 0, "earliest": None, "latest": None}
    out = {oid: dict(empty) for oid in object_ids}

    agg_rows = (
        db.query(
            MetricPoint.object_id,
            func.avg(MetricPoint.value),
            func.max(MetricPoint.value),
            func.count(MetricPoint.value),
            func.min(MetricPoint.sampled_at),
            func.max(MetricPoint.sampled_at),
        )
        .filter(
            MetricPoint.object_type == object_type,
            MetricPoint.object_id.in_(object_ids),
            MetricPoint.metric == metric,
            MetricPoint.value.isnot(None),
            *(() if max_value is None else (MetricPoint.value <= max_value,)),
        )
        .group_by(MetricPoint.object_id)
        .all()
    )
    for object_id, avg, mx, count, earliest, latest in agg_rows:
        out[object_id] = {
            "avg": round(avg, 1) if avg is not None else None,
            "p95": None,  # filled in below, one targeted OFFSET/LIMIT query at a time
            "p99": None,
            "max": round(mx, 1) if mx is not None else None,
            "sample_count": count or 0,
            "earliest": earliest,
            "latest": latest,
        }

    for object_id, stats in out.items():
        n = stats["sample_count"]
        if not n:
            continue
        rank0 = min(n - 1, int(round(0.95 * (n - 1))))
        p95 = (
            db.query(MetricPoint.value)
            .filter(
                MetricPoint.object_type == object_type,
                MetricPoint.object_id == object_id,
                MetricPoint.metric == metric,
                MetricPoint.value.isnot(None),
                *(() if max_value is None else (MetricPoint.value <= max_value,)),
            )
            .order_by(MetricPoint.value)
            .offset(rank0)
            .limit(1)
            .scalar()
        )
        if p95 is not None:
            stats["p95"] = round(p95, 1)

        if with_p99:
            rank99 = min(n - 1, int(round(0.99 * (n - 1))))
            p99 = (
                db.query(MetricPoint.value)
                .filter(
                    MetricPoint.object_type == object_type,
                    MetricPoint.object_id == object_id,
                    MetricPoint.metric == metric,
                    MetricPoint.value.isnot(None),
                    *(() if max_value is None else (MetricPoint.value <= max_value,)),
                )
                .order_by(MetricPoint.value)
                .offset(rank99)
                .limit(1)
                .scalar()
            )
            if p99 is not None:
                stats["p99"] = round(p99, 1)

    return out


def observation_days(earliest, latest) -> float:
    if not earliest or not latest:
        return 0.0
    return max(0.0, (latest - earliest).total_seconds() / 86400)


def confidence_for_observation_days(days: float) -> str:
    if days < 7:
        return "insufficient_data"
    if days < 30:
        return "preliminary"
    if days < 60:
        return "moderate"
    return "high"


def latest_workload_metrics(db: Session) -> dict[str, dict]:
    """Most recent cpu_pct/mem_pct sample per workload -- the workload
    equivalent of Node.cpu_usage_pct/mem_usage_pct (which are live columns
    refreshed every discovery poll; workloads have no such column, only
    the same-cadence MetricPoint history everything else here reads from).
    Used for the Maintenance page's quick-glance workload meters -- 'as of
    the last poll', same freshness a node's own meter already implies, not
    a live PVE call per workload.

    Real samples are only pulled for currently-running workloads -- discovery
    only samples cpu/mem for a running guest, so a stopped one's "most
    recent" sample is really its LAST reading from whenever it was last
    running, which can be arbitrarily old. Without this restriction that
    stale number renders as if it were live -- a VM stopped for months can
    otherwise still show a high RAM/CPU reading from whenever it was last
    running. A stopped (but not missing) workload isn't just omitted,
    though -- it's genuinely idle, so it gets an explicit 0/0 rather than
    the UI's blank "no data" fallback, which would otherwise read the same
    as "we don't know": a powered-off VM should show 0 CPU and 0 RAM
    usage, not a blank. Only a *missing* workload (not discovered/gone
    from PVE) gets no entry at all -- that's the one case where "unknown"
    is the honest answer.
    """
    # One index probe per (running workload, metric) via LATERAL -- the old
    # SELECT DISTINCT ON over metric_points scanned the whole table (8M+ rows,
    # ~20s alone, 90s under concurrent polling) and starved the API's DB pool.
    rows = db.execute(
        text(
            """
            SELECT w.id, 'cpu_pct' AS metric, mp.value
            FROM workloads w
            JOIN LATERAL (
                SELECT value FROM metric_points
                WHERE object_type = 'workload' AND object_id = w.id AND metric = 'cpu_pct'
                ORDER BY sampled_at DESC LIMIT 1
            ) mp ON true
            WHERE w.status = 'running' AND NOT w.is_missing
            UNION ALL
            -- RAM is the median of the last 5 one-minute samples, not the last
            -- single one: a guest's reported memory swings by gigabytes minute
            -- to minute and one sample that falls back to the host-side figure
            -- read as "100%" while PVE's own live value was ~25%.
            SELECT w.id, 'mem_pct' AS metric, mp.v
            FROM workloads w
            JOIN LATERAL (
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY s.value) AS v
                FROM (
                    SELECT value FROM metric_points
                    WHERE object_type = 'workload' AND object_id = w.id AND metric = 'mem_pct'
                      AND value <= 100  -- >100% is the host-side figure, impossible as guest usage
                    ORDER BY sampled_at DESC LIMIT 5
                ) s
            ) mp ON mp.v IS NOT NULL
            WHERE w.status = 'running' AND NOT w.is_missing
            """
        )
    ).all()
    out: dict[str, dict] = {}
    for object_id, metric, value in rows:
        out.setdefault(str(object_id), {})[metric] = value

    # Live value, exactly what PVE's own summary screen / Datacenter table show
    # (VM list mem / maxmem, refreshed every inventory cycle). Preferred over the
    # history-based figure above, which stays as the fallback until the first
    # inventory cycle after an upgrade has filled this in.
    live = (
        db.query(Workload.id, Workload.mem_used_bytes, Workload.memory_bytes)
        .filter(
            Workload.status == "running", Workload.is_missing.is_(False),
            Workload.mem_used_bytes.isnot(None), Workload.memory_bytes > 0,
        )
        .all()
    )
    for wid, used, total in live:
        out.setdefault(str(wid), {})["mem_pct"] = used / total * 100

    # Fresher still: the worker's ~30s live-memory loop (workload_live_mem). Rows older
    # than 3 minutes are ignored so a stalled loop quietly falls back to the above.
    fresh = db.execute(
        text(
            """
            SELECT l.workload_id, l.mem_used_bytes, w.memory_bytes
            FROM workload_live_mem l JOIN workloads w ON w.id = l.workload_id
            WHERE l.sampled_at > now() - interval '180 seconds'
              AND w.status = 'running' AND NOT w.is_missing AND w.memory_bytes > 0
            """
        )
    ).all()
    for wid, used, total in fresh:
        out.setdefault(str(wid), {})["mem_pct"] = used / total * 100

    # "host" = PVE gives no guest memory stats for this VM, so mem_pct is the
    # host-side figure (~100%), not usage; the UI shows it muted, not red.
    host_only_ids = [wid for (wid,) in db.query(Workload.id).filter(Workload.mem_guest_stats.is_(False)).all()]
    for wid in host_only_ids:
        entry = out.get(str(wid))
        if entry is None:
            continue
        if "mem_pct" not in entry:
            # Every recent sample was >100% (host-side process size), so the median
            # above had nothing valid to use -- still show the latest raw host-side
            # figure, labelled "host", rather than a blank.
            raw = (
                db.query(MetricPoint.value)
                .filter(MetricPoint.object_type == "workload", MetricPoint.object_id == wid, MetricPoint.metric == "mem_pct")
                .order_by(MetricPoint.sampled_at.desc())
                .limit(1)
                .scalar()
            )
            if raw is not None:
                entry["mem_pct"] = raw
        if "mem_pct" in entry:
            entry["mem_source"] = "host"

    stopped_ids = (
        db.query(Workload.id)
        .filter(Workload.status != "running", Workload.is_missing.is_(False))
        .all()
    )
    for (object_id,) in stopped_ids:
        out[str(object_id)] = {"cpu_pct": 0, "mem_pct": 0}

    return out


def prune_old_metrics(db: Session, retention_days: int = 400) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = db.query(MetricPoint).filter(MetricPoint.sampled_at < cutoff).delete()
    from .mem_pressure import prune_memory_pressure

    deleted += prune_memory_pressure(db)
    db.commit()
    return deleted
