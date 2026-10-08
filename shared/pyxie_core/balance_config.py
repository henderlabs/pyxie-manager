"""Per-cluster settings for Balance Load and automatic balancing (stored as cluster-scoped policies).

No imports of the workflows here, so the planner can read the settings without a cycle."""

import math
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import MetricPoint, Policy

CONFIG_KEY = "balance.auto"
STATE_KEY = "balance.auto_state"

MODES = ("off", "recommend", "auto_approve")
METRICS = ("most_limited", "memory", "cpu", "both")

# One preset per aggressiveness level. Every number errs toward doing nothing.
PRESETS: dict[str, dict] = {
    "conservative": {"trigger_score": 60, "min_benefit": 25.0, "max_moves": 2, "cluster_cooldown_hours": 6.0, "guest_cooldown_hours": 168.0},
    "moderate": {"trigger_score": 70, "min_benefit": 15.0, "max_moves": 4, "cluster_cooldown_hours": 2.0, "guest_cooldown_hours": 24.0},
    "aggressive": {"trigger_score": 80, "min_benefit": 10.0, "max_moves": 8, "cluster_cooldown_hours": 0.5, "guest_cooldown_hours": 6.0},
}

DEFAULT_CONFIG = {
    "mode": "off", "level": "moderate", "metric": "most_limited",
    "node_ids": [],  # empty = every node in the cluster
    "windows": [],  # empty = any time; else [{"days": [0..6, Mon=0], "start": "HH:MM", "end": "HH:MM"}]
    "paused_until": None,  # ISO timestamp
    "ack": None,  # who confirmed auto-approve, when, and (for Aggressive) the second confirmation; only kept while mode is auto_approve
}


class ConfigError(ValueError):
    pass


def _policy(db: Session, cluster_id, key: str) -> Policy | None:
    return db.query(Policy).filter(Policy.scope_type == "cluster", Policy.scope_id == cluster_id, Policy.key == key).one_or_none()


def get_config(db: Session, cluster_id) -> dict:
    row = _policy(db, cluster_id, CONFIG_KEY)
    cfg = {**DEFAULT_CONFIG, **(row.value if row and isinstance(row.value, dict) else {})}
    if cfg["level"] not in PRESETS:
        cfg["level"] = DEFAULT_CONFIG["level"]
    if cfg["metric"] not in METRICS:
        cfg["metric"] = DEFAULT_CONFIG["metric"]
    if cfg["mode"] not in MODES:
        cfg["mode"] = "off"
    return cfg


def validate_config(cfg: dict) -> dict:
    """Returns a clean copy or raises ConfigError with a message meant for the person saving it."""
    out = {**DEFAULT_CONFIG}
    if cfg.get("mode", "off") not in MODES:
        raise ConfigError("mode must be 'off', 'recommend' or 'auto_approve'")
    out["mode"] = cfg.get("mode", "off")
    if cfg.get("level", "moderate") not in PRESETS:
        raise ConfigError("level must be conservative, moderate or aggressive")
    out["level"] = cfg.get("level", "moderate")
    if cfg.get("metric", "most_limited") not in METRICS:
        raise ConfigError("metric must be most_limited, memory, cpu or both")
    out["metric"] = cfg.get("metric", "most_limited")
    ids = cfg.get("node_ids") or []
    if not isinstance(ids, list):
        raise ConfigError("node_ids must be a list")
    out["node_ids"] = [str(i) for i in ids]
    windows = cfg.get("windows") or []
    if not isinstance(windows, list) or len(windows) > 14:
        raise ConfigError("windows must be a list of at most 14 entries")
    clean = []
    for w in windows:
        days = sorted({int(d) for d in (w.get("days") or [])})
        if not days or any(d < 0 or d > 6 for d in days):
            raise ConfigError("each window needs at least one day (0 = Monday ... 6 = Sunday)")
        for k in ("start", "end"):
            try:
                datetime.strptime(w.get(k, ""), "%H:%M")
            except ValueError:
                raise ConfigError(f"window {k} must look like 22:00")
        clean.append({"days": days, "start": w["start"], "end": w["end"]})
    out["windows"] = clean
    out["paused_until"] = cfg.get("paused_until")
    if out["mode"] == "auto_approve":
        ack = cfg.get("ack")
        if not isinstance(ack, dict) or not ack.get("by") or not ack.get("at"):
            raise ConfigError("auto-approve needs the confirmation that PyXie may move guests on its own")
        if out["level"] == "aggressive" and not ack.get("aggressive"):
            raise ConfigError("the Aggressive level needs its own second confirmation")
        out["ack"] = {"by": str(ack["by"]), "at": str(ack["at"]), "aggressive": bool(ack.get("aggressive"))}
    return out


def save_config(db: Session, cluster_id, cfg: dict) -> dict:
    clean = validate_config(cfg)
    row = _policy(db, cluster_id, CONFIG_KEY)
    if row is None:
        db.add(Policy(scope_type="cluster", scope_id=cluster_id, key=CONFIG_KEY, value=clean))
    else:
        row.value = clean
    db.commit()
    return clean


def get_state(db: Session, cluster_id) -> dict:
    row = _policy(db, cluster_id, STATE_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


def save_state(db: Session, cluster_id, state: dict) -> None:
    row = _policy(db, cluster_id, STATE_KEY)
    if row is None:
        db.add(Policy(scope_type="cluster", scope_id=cluster_id, key=STATE_KEY, value=state))
    else:
        row.value = state
    db.commit()


# Automatic balancing judges the cluster by each node's BUSY level over the last week, not by right now: a server
# that is only busy in business hours must not look idle at 3 a.m. and attract guests it cannot carry at 10 a.m.
SMOOTH_DAYS = 7
SMOOTH_QUANTILE = 0.9
MIN_HISTORY_HOURS = 24.0


def smooth_from_rows(rows: list[tuple], now: datetime) -> dict:
    """rows are (node_id, metric, quantile_value, first_sampled_at) from the metric history. Returns
    {node_id: {"mem_pct", "cpu_pct", "hours"}} where hours is how much history backs the numbers (capped at the window)."""
    out: dict = {}
    for node_id, metric, value, first in rows:
        slot = out.setdefault(node_id, {"mem_pct": None, "cpu_pct": None, "hours": 0.0})
        if metric in ("mem_pct", "cpu_pct") and value is not None:
            slot[metric] = float(value)
        if first is not None:
            hrs = min((now - first).total_seconds() / 3600.0, SMOOTH_DAYS * 24.0)
            slot["hours"] = hrs if slot["hours"] == 0.0 else min(slot["hours"], hrs)
    return out


def smoothed_loads(db: Session, nodes, now: datetime) -> tuple[dict, bool]:
    """Per node id: busy-level memory and CPU (7-day 90th percentile). Second value: does every live node have at
    least MIN_HISTORY_HOURS of history, so the numbers can be trusted."""
    live = [n for n in nodes if n.status == "online" and not n.maintenance_mode]
    ids = [n.id for n in live]
    rows = []
    if ids:
        rows = (
            db.query(MetricPoint.object_id, MetricPoint.metric, func.percentile_cont(SMOOTH_QUANTILE).within_group(MetricPoint.value), func.min(MetricPoint.sampled_at))
            .filter(MetricPoint.object_type == "node", MetricPoint.object_id.in_(ids), MetricPoint.metric.in_(("mem_pct", "cpu_pct")),
                    MetricPoint.sampled_at >= now - timedelta(days=SMOOTH_DAYS), MetricPoint.value.isnot(None))
            .group_by(MetricPoint.object_id, MetricPoint.metric).all()
        )
    loads = smooth_from_rows(rows, now)
    enough = bool(live) and all(n.id in loads and loads[n.id]["mem_pct"] is not None and loads[n.id]["hours"] >= MIN_HISTORY_HOURS for n in live)
    return loads, enough


def smoothed_view(nodes, loads: dict) -> list:
    """The same nodes with their busy-level numbers standing in for the live ones, so balance_score() and the
    memory projection can be reused unchanged."""
    out = []
    for n in nodes:
        s = loads.get(n.id)
        if s is None or s["mem_pct"] is None:
            continue
        out.append(SimpleNamespace(
            id=n.id, name=n.name, status=n.status, maintenance_mode=n.maintenance_mode, mem_total_bytes=n.mem_total_bytes,
            mem_usage_pct=s["mem_pct"], cpu_usage_pct=s["cpu_pct"] if s["cpu_pct"] is not None else n.cpu_usage_pct,
        ))
    return out


def swaps_imbalance(src_pct: float, dst_pct: float, memory_bytes: int, dst_total_bytes: int) -> tuple[bool, float]:
    """Would moving a guest of this size leave the destination fuller than the source is right now?
    The scorer ranks destinations by headroom and can send a big guest to a small quiet node so that node ends
    up fuller than the one the guest left: that swaps the imbalance instead of curing it. Returns (swaps, dst_after_pct)."""
    after = dst_pct + memory_bytes / dst_total_bytes * 100.0
    return after > src_pct + 0.05, after


def resolve_metric(setting: str, nodes) -> str:
    """'most_limited' becomes whichever of memory and CPU is running hotter on the busiest live node, so the
    cluster is balanced on the resource that will run out first. Memory wins ties and missing data: it is
    what limits where a VM can move."""
    if setting in ("memory", "cpu", "both"):
        return setting
    live = [n for n in nodes if n.status == "online" and not n.maintenance_mode]
    mem = max((n.mem_usage_pct for n in live if n.mem_usage_pct is not None), default=None)
    cpu = max((n.cpu_usage_pct for n in live if n.cpu_usage_pct is not None), default=None)
    if mem is None or cpu is None:
        return "memory"
    return "cpu" if cpu > mem else "memory"


def in_window(windows: list[dict], local_now: datetime) -> bool:
    """No windows means any time. A window may cross midnight (22:00-05:00): it then belongs to the day it starts on."""
    if not windows:
        return True
    hm = local_now.strftime("%H:%M")
    for w in windows:
        start, end = w["start"], w["end"]
        if start <= end:
            if local_now.weekday() in w["days"] and start <= hm < end:
                return True
        else:  # crosses midnight
            if local_now.weekday() in w["days"] and hm >= start:
                return True
            if ((local_now.weekday() - 1) % 7) in w["days"] and hm < end:
                return True
    return False


def weighted_spread(values: list[float], weights: list[float]) -> float:
    """Size-aware spread in percentage points: twice the weighted standard deviation. Two equal nodes at 90% and 10%
    give 80, the same as the plain busiest-minus-quietest gap, but a small quiet node among large ones counts for
    little, and a lone busy node among many even ones still shows."""
    total = sum(weights)
    mean = sum(v * w for v, w in zip(values, weights)) / total
    return 2.0 * math.sqrt(sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / total)


def balance_score(nodes) -> float | None:
    """Same score as the Dashboard gauge: 100 minus the size-weighted spread of live nodes (memory always; CPU once the
    busiest node is past 50%). Each node counts in proportion to its memory size, so a small node sitting at a different
    percentage does not make a cluster that is as even as its sizes allow read as uneven. None when fewer than two
    nodes have data. Keep in step with computeBalance() in web/src/components/ClusterBalance.tsx."""
    live = [n for n in nodes if n.status == "online" and not n.maintenance_mode and n.mem_usage_pct is not None]
    if len(live) < 2:
        return None
    sizes = [float(getattr(n, "mem_total_bytes", None) or 0.0) for n in live]
    weights = sizes if all(s > 0 for s in sizes) else [1.0] * len(live)
    mem_gap = weighted_spread([n.mem_usage_pct for n in live], weights)
    cpu_pairs = [(n.cpu_usage_pct, w) for n, w in zip(live, weights) if n.cpu_usage_pct is not None]
    cpu_gap = 0.0
    if cpu_pairs and max(v for v, _ in cpu_pairs) >= 50:
        cpu_gap = weighted_spread([v for v, _ in cpu_pairs], [w for _, w in cpu_pairs])
    return max(0.0, min(100.0, 100 - max(mem_gap, cpu_gap)))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hours_ago(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        return (now - datetime.fromisoformat(ts)).total_seconds() / 3600
    except ValueError:
        return None

