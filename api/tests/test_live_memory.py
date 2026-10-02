"""live_memory_rows: PVE cluster-resources entries -> live memory rows (pure, no DB/PVE)."""

from datetime import datetime, timezone

from pyxie_core.live_memory import live_memory_rows

NOW = datetime.now(timezone.utc)
IDS = {100: "w100", 101: "w101", 215: "w215"}


def test_running_guests_with_memory_are_kept_as_reported():
    rows = live_memory_rows([{"vmid": 215, "status": "running", "mem": 17256449024, "maxmem": 17179869184}], IDS, NOW)
    # uncapped: 100.45% is what PVE shows, so 100.45% is what is stored
    assert rows == [{"workload_id": "w215", "mem_used_bytes": 17256449024, "sampled_at": NOW}]


def test_stopped_unknown_and_incomplete_entries_are_skipped():
    rows = live_memory_rows(
        [
            {"vmid": 100, "status": "stopped", "mem": 0},                 # not running
            {"vmid": 999, "status": "running", "mem": 5},                  # not in inventory
            {"vmid": 101, "status": "running"},                            # no memory figure
            {"status": "running", "mem": 5},                               # no vmid
            {"vmid": 101, "status": "running", "mem": 0},                  # zero is a real reading
        ],
        IDS, NOW,
    )
    assert rows == [{"workload_id": "w101", "mem_used_bytes": 0, "sampled_at": NOW}]


def test_empty_or_missing_input():
    assert live_memory_rows([], IDS, NOW) == []
    assert live_memory_rows(None, IDS, NOW) == []
