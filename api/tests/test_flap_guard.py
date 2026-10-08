"""The loop breaker and the busy-hours smoothing for automatic balancing. Pure logic: no database."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pyxie_core.auto_balance import flapping_guests
from pyxie_core.balance_config import MIN_HISTORY_HOURS, balance_score, smooth_from_rows, smoothed_view

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
GB = 1024 ** 3


def hrs(h):
    return NOW - timedelta(hours=h)


def test_a_guest_never_goes_back_to_a_host_it_left_in_the_last_3_days():
    settled, avoid = flapping_guests([("g1", hrs(10), "A")], NOW)
    assert avoid == {"g1": {"A"}} and settled == {}


def test_a_move_older_than_3_days_no_longer_blocks_the_way_back():
    _, avoid = flapping_guests([("g1", hrs(80), "A")], NOW)
    assert avoid == {}


def test_a_guest_moved_twice_in_a_week_is_settled():
    settled, _ = flapping_guests([("g1", hrs(100), "A"), ("g1", hrs(20), "B")], NOW)
    assert settled == {"g1": 2}


def test_one_move_in_a_week_is_not_settled_and_old_moves_do_not_count():
    assert flapping_guests([("g1", hrs(20), "A")], NOW)[0] == {}
    assert flapping_guests([("g1", hrs(200), "A"), ("g1", hrs(20), "B")], NOW)[0] == {}


def test_other_guests_are_unaffected_and_missing_data_is_ignored():
    settled, avoid = flapping_guests([("g1", hrs(5), "A"), ("g2", None, "B"), ("g3", hrs(5), None)], NOW)
    assert avoid == {"g1": {"A"}} and settled == {}


def test_history_rows_become_busy_levels_with_the_shortest_history_winning():
    rows = [("n1", "mem_pct", 61.7, hrs(150)), ("n1", "cpu_pct", 9.0, hrs(20)), ("n2", "mem_pct", 30.0, hrs(300))]
    out = smooth_from_rows(rows, NOW)
    assert out["n1"]["mem_pct"] == 61.7 and out["n1"]["cpu_pct"] == 9.0 and round(out["n1"]["hours"]) == 20
    assert out["n2"]["hours"] == 7 * 24  # capped at the 7-day window


def node(id_, mem, size=64):
    return SimpleNamespace(id=id_, name=id_, status="online", maintenance_mode=False, mem_usage_pct=mem, cpu_usage_pct=0, mem_total_bytes=size * GB)


def test_a_server_that_is_only_busy_in_business_hours_does_not_look_idle_at_night():
    # At 3 a.m. node B reads 10% right now, but its busy level over the week is 70%.
    night = [node("A", 60), node("B", 10)]
    loads = {"A": {"mem_pct": 62.0, "cpu_pct": 5.0, "hours": 168}, "B": {"mem_pct": 70.0, "cpu_pct": 5.0, "hours": 168}}
    assert balance_score(night) < 60  # looks badly skewed right now: acting on this would be the mistake
    assert balance_score(smoothed_view(night, loads)) > 85  # at their busy level the two are even: leave it alone


def test_enough_history_threshold_is_a_day():
    assert MIN_HISTORY_HOURS == 24.0
