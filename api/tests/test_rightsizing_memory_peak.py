"""Rightsizing memory sizing: P99 peak, and "add memory" only on real memory pressure.

PVE's per-VM "used" memory counts the guest's file cache, so a healthy cache-heavy
VM reads 95-98%. Two protections against telling people to add memory to those:
the peak is the 99th percentile (one stray sample no longer decides), and for VMs
an "increase" additionally needs evidence the guest is actually swapping in.
Pure logic against _compute_assessment / memory_pressure_verdict: no database.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pyxie_core.mem_pressure import (
    MIN_DAYS, SWAP_IN_BYTES_PER_DAY, memory_pressure_verdict,
)
from pyxie_core.rightsizing import _compute_assessment

GiB = 2**30
MiB = 2**20
NOW = datetime.now(timezone.utc)


def _workload(**over):
    base = dict(
        id="w1", cluster_id="c1", node_id="n1", vmid=100, name="vm", cpu_cores=4, memory_bytes=16 * GiB,
        status="running", os_type="l26", mem_guest_stats=True, type="vm",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _stats(avg, p95, mx, p99="unset", n=100000):
    s = {"avg": avg, "p95": p95, "max": mx, "sample_count": n,
         "earliest": NOW - timedelta(days=120), "latest": NOW}
    if p99 != "unset":
        s["p99"] = p99
    return s


CPU = _stats(5, 10, 20, p99=15)
PRESSURE = {"days": 10.0, "samples": 2880, "swap_in_bytes": 20 * GiB, "swap_out_bytes": 5 * GiB}
NO_PRESSURE = {"days": 10.0, "samples": 2880, "swap_in_bytes": 0, "swap_out_bytes": 0}
GATHERING = {"days": 2.0, "samples": 576, "swap_in_bytes": 0, "swap_out_bytes": 0}


def _assess(mem_stats, pressure=None, **wl):
    return _compute_assessment(_workload(**wl), CPU, mem_stats, 75, 75, pressure=pressure)


def _mem_suggestion(mem_stats, pressure=None, **wl):
    return _assess(mem_stats, pressure, **wl)["memory_suggestion"]


HIGH = _stats(90, 96, 100, p99=99)   # sustained high "used" memory (cache-inclusive)


# ---- P99 peak ---------------------------------------------------------------

def test_a_single_spike_no_longer_forces_an_increase():
    # p95 40%, p99 55%, one stray sample at 100%: used to read "increase"
    s = _mem_suggestion(_stats(30, 40, 100, p99=55), PRESSURE)
    assert s is None or s["direction"] != "increase"


def test_without_p99_it_falls_back_to_the_absolute_peak():
    # older stats dicts (no p99 key) behave as before (with pressure evidence present)
    s = _mem_suggestion(_stats(30, 40, 100), PRESSURE)
    assert s is not None and s["direction"] == "increase"


def test_vm_without_guest_memory_stats_is_not_assessed_for_memory():
    assert _mem_suggestion(HIGH, PRESSURE, mem_guest_stats=False) is None


# ---- the memory-pressure gate -------------------------------------------------

def test_high_usage_with_real_swapping_recommends_an_increase():
    s = _mem_suggestion(HIGH, PRESSURE)
    assert s is not None and s["direction"] == "increase"
    assert "swapped in" in s["reason"] and "real memory pressure" in s["reason"]


def test_high_usage_without_swapping_is_file_cache_and_gets_no_increase():
    a = _assess(HIGH, NO_PRESSURE)
    assert a["memory_suggestion"] is None
    assert "file cache" in a["memory_note"]


def test_high_usage_without_enough_history_says_it_is_gathering_data():
    for pressure in (GATHERING, None):
        a = _assess(HIGH, pressure)
        assert a["memory_suggestion"] is None
        assert "gathering memory-pressure data" in a["memory_note"]


def test_containers_keep_the_usage_only_rule():
    # LXC has no balloon counters; the old behaviour stands for them
    s = _mem_suggestion(HIGH, None, type="lxc")
    assert s is not None and s["direction"] == "increase"


# ---- verdict thresholds ---------------------------------------------------------

def test_verdict_thresholds():
    assert memory_pressure_verdict(None)["state"] == "gathering"
    assert memory_pressure_verdict({"days": MIN_DAYS - 0.5, "swap_in_bytes": 50 * GiB})["state"] == "gathering"
    # exactly at the per-day threshold over 10 days -> pressure
    at = {"days": 10.0, "swap_in_bytes": SWAP_IN_BYTES_PER_DAY * 10}
    assert memory_pressure_verdict(at)["state"] == "pressure"
    # a trickle (1 GiB total over 10 days = ~100 MiB/day) is not pressure
    trickle = {"days": 10.0, "swap_in_bytes": 1 * GiB}
    assert memory_pressure_verdict(trickle)["state"] == "no_pressure"
