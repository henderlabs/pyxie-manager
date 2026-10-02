"""Rightsizing memory sizing uses the 99th percentile, not the single highest sample.

A lone stray reading (a ballooned guest briefly reporting its host-side size,
a backup window) used to decide the whole recommendation: 97 of 130 VMs were
told to increase memory. Pure logic against _compute_assessment: no database.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pyxie_core.rightsizing import _compute_assessment

GiB = 2**30
NOW = datetime.now(timezone.utc)


def _workload(**over):
    base = dict(
        id="w1", cluster_id="c1", node_id="n1", vmid=100, name="vm", cpu_cores=4, memory_bytes=16 * GiB,
        status="running", os_type="l26", mem_guest_stats=True,
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


def _mem_suggestion(mem_stats, **wl):
    return _compute_assessment(_workload(**wl), CPU, mem_stats, 75, 75)["memory_suggestion"]


def test_a_single_spike_no_longer_forces_an_increase():
    # p95 40%, p99 55%, one stray sample at 100%: used to read "increase"
    s = _mem_suggestion(_stats(30, 40, 100, p99=55))
    assert s is None or s["direction"] != "increase"


def test_sustained_high_usage_still_recommends_an_increase():
    s = _mem_suggestion(_stats(90, 96, 100, p99=99))
    assert s is not None and s["direction"] == "increase"
    assert "99th-percentile" in s["reason"]


def test_without_p99_it_falls_back_to_the_absolute_peak():
    # older stats dicts (no p99 key) behave exactly as before
    s = _mem_suggestion(_stats(30, 40, 100))
    assert s is not None and s["direction"] == "increase"


def test_vm_without_guest_memory_stats_is_not_assessed_for_memory():
    s = _mem_suggestion(_stats(90, 96, 100, p99=99), mem_guest_stats=False)
    assert s is None
