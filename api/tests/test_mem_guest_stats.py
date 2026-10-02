"""Guest-memory-stats detection (shared/pyxie_core/metrics.py).

PVE's VM memory series is the guest-reported "used" memory only when the
balloon device / guest agent supplies stats. Otherwise it falls back to the
host-side size of the QEMU process (memhost), which for a VM that has touched
its RAM is ~100% of its allocation (allocation plus overhead, so ~100.4%).
A VM "has guest stats" when at least one sample differs from memhost by more
than a small tolerance. Pure logic: no database, no PVE.
"""

from pyxie_core.metrics import mem_guest_stats_from_rrd

GiB = 2**30
MAXMEM = 16 * GiB


def _pt(mem_gib, host_gib=16.06, maxmem=MAXMEM):
    return {"mem": mem_gib * GiB, "memhost": host_gib * GiB, "maxmem": maxmem}


def test_no_balloon_stats_means_host_only():
    # mem == memhost in every sample
    assert mem_guest_stats_from_rrd([_pt(16.06)] * 60) is False


def test_ballooned_vm_with_swings_and_a_fallback_blip_has_guest_stats():
    # PAW-JT-JohnLee on 2026-10-02: guest figure swung 6.7-16.06 GiB minute to
    # minute and one sample fell back to the host figure.
    swing = [_pt(x) for x in (7.93, 14.03, 6.70, 9.96, 11.18, 14.03, 16.06, 9.97)]
    assert mem_guest_stats_from_rrd(swing) is True


def test_low_usage_ballooned_vm_has_guest_stats():
    assert mem_guest_stats_from_rrd([_pt(3.03)] * 10) is True


def test_difference_under_tolerance_is_still_host_only():
    # 0.5% of 16 GiB is ~82 MiB; 0.02 GiB (~20 MiB) is inside it
    assert mem_guest_stats_from_rrd([_pt(16.06 - 0.02)] * 10) is False


def test_empty_window_is_unknown():
    assert mem_guest_stats_from_rrd([]) is None


def test_samples_without_memhost_are_unknown():
    assert mem_guest_stats_from_rrd([{"mem": 1, "maxmem": MAXMEM}] * 5) is None
