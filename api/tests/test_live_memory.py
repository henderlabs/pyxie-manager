"""live_memory_rows: PVE cluster-resources entries -> live memory rows (pure, no DB/PVE)."""

from datetime import datetime, timezone

from pyxie_core.live_memory import is_host_fallback, live_memory_rows

GiB = 2**30
NOW = datetime.now(timezone.utc)


def _info(wid, guest=True, host=None):
    return {"id": wid, "guest_stats": guest, "host_bytes": host}


IDS = {100: _info("w100"), 101: _info("w101"), 215: _info("w215", guest=False, host=int(16.07 * GiB))}


def test_running_guests_with_memory_are_kept_as_reported():
    rows = live_memory_rows([{"vmid": 215, "status": "running", "mem": 17256449024, "maxmem": 17179869184}], IDS, NOW)
    # uncapped, and a no-guest-stats VM is shown as PVE reports it: 100.45% stays 100.45%
    assert rows == [{"workload_id": "w215", "mem_used_bytes": 17256449024, "sampled_at": NOW}]


def test_stopped_unknown_and_incomplete_entries_are_skipped():
    rows = live_memory_rows(
        [
            {"vmid": 100, "status": "stopped", "mem": 0},
            {"vmid": 999, "status": "running", "mem": 5},
            {"vmid": 101, "status": "running"},
            {"status": "running", "mem": 5},
            {"vmid": 101, "status": "running", "mem": 0},
        ],
        IDS, NOW,
    )
    assert rows == [{"workload_id": "w101", "mem_used_bytes": 0, "sampled_at": NOW}]


def test_empty_or_missing_input():
    assert live_memory_rows([], IDS, NOW) == []
    assert live_memory_rows(None, IDS, NOW) == []


def test_host_side_fallback_blip_is_skipped_for_vms_that_report_guest_stats():
    # PAW-JT-JohnLee: real guest figure ~24%, fallback = host size 16.06 of 16.00 GiB
    info = {1070: _info("w1070", guest=True, host=int(16.06 * GiB))}
    blip = {"vmid": 1070, "status": "running", "mem": int(16.06 * GiB), "maxmem": 16 * GiB}
    good = {"vmid": 1070, "status": "running", "mem": int(3.88 * GiB), "maxmem": 16 * GiB}
    assert live_memory_rows([blip], info, NOW) == []
    assert [r["mem_used_bytes"] for r in live_memory_rows([good], info, NOW)] == [int(3.88 * GiB)]


def test_a_genuinely_full_guest_below_the_host_size_is_still_shown():
    # cache-heavy guest at 98% used while the host-side size is 100.4%
    info = {1: _info("w1", guest=True, host=int(16.06 * GiB))}
    row = {"vmid": 1, "status": "running", "mem": int(15.7 * GiB), "maxmem": 16 * GiB}
    assert len(live_memory_rows([row], info, NOW)) == 1


def test_fallback_check_only_applies_to_vms_with_guest_stats():
    assert is_host_fallback(16.06 * GiB, 16 * GiB, True, 16.06 * GiB) is True
    assert is_host_fallback(16.06 * GiB, 16 * GiB, False, 16.06 * GiB) is False   # no guest stats: show what PVE says
    assert is_host_fallback(16.06 * GiB, 16 * GiB, None, 16.06 * GiB) is False
    assert is_host_fallback(16.06 * GiB, 16 * GiB, True, None) is False           # host size unknown yet
