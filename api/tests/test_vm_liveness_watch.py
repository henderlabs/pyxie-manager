"""Pure coverage for turning probes into a persistent 'not responding' signal."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.vm_liveness import finding_worthy, next_liveness_row, suppress_node_wide_failures

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
BAD = {"state": "unresponsive", "detail": "no answer", "elapsed": 6.0}
OK = {"state": "ok", "detail": "fine", "elapsed": 0.05}
UNKNOWN = {"state": "unknown", "detail": "PVE down", "elapsed": 0.1}


def test_streak_builds_and_remembers_when_it_started():
    r1 = next_liveness_row(None, BAD, T0)
    assert r1["consecutive_bad"] == 1 and r1["bad_since"] == T0
    r2 = next_liveness_row(r1, BAD, T0 + timedelta(minutes=1))
    assert r2["consecutive_bad"] == 2 and r2["bad_since"] == T0  # since = first bad probe, not latest
    assert r2["checked_at"] == T0 + timedelta(minutes=1)


def test_a_good_answer_ends_the_streak():
    r = next_liveness_row(next_liveness_row(None, BAD, T0), OK, T0 + timedelta(minutes=1))
    assert r["consecutive_bad"] == 0 and r["bad_since"] is None and r["state"] == "ok"


def test_unknown_does_not_clear_or_extend_a_streak():
    bad2 = next_liveness_row(next_liveness_row(None, BAD, T0), BAD, T0)
    r = next_liveness_row(bad2, UNKNOWN, T0 + timedelta(minutes=1))
    assert r["consecutive_bad"] == 2 and r["bad_since"] == T0 and r["state"] == "unknown"
    assert next_liveness_row(None, UNKNOWN, T0)["consecutive_bad"] == 0


def test_a_problem_state_counts_like_unresponsive():
    r = next_liveness_row(None, {"state": "problem", "detail": "guest-panicked", "elapsed": 0.1}, T0)
    assert r["consecutive_bad"] == 1


def test_finding_needs_two_in_a_row_and_a_fresh_result():
    one = next_liveness_row(None, BAD, T0)
    two = next_liveness_row(one, BAD, T0 + timedelta(minutes=1))
    assert not finding_worthy(one, T0 + timedelta(seconds=30))  # a single bad probe is not enough
    assert finding_worthy(two, T0 + timedelta(minutes=1, seconds=30))
    assert not finding_worthy(two, T0 + timedelta(minutes=10))  # stale: VM stopped / worker down -> clears
    assert not finding_worthy({"checked_at": None, "consecutive_bad": 5}, T0)
    naive = {**two, "checked_at": two["checked_at"].replace(tzinfo=None)}
    assert finding_worthy(naive, T0 + timedelta(minutes=1, seconds=30))  # DB may hand back naive datetimes


def test_node_wide_failure_is_not_blamed_on_the_vms():
    results = {v: dict(BAD) for v in (1, 2, 3, 4, 5)}
    results[6] = dict(OK)
    out = suppress_node_wide_failures(results, {v: "n1" for v in results})
    assert all(out[v]["state"] == "unknown" for v in (1, 2, 3, 4, 5)) and "n1" in out[1]["detail"]
    assert out[6]["state"] == "ok"


def test_one_dead_vm_among_healthy_ones_is_kept_and_small_nodes_are_left_alone():
    many = {v: dict(OK) for v in range(1, 10)}
    many[3] = dict(BAD)
    assert suppress_node_wide_failures(many, {v: "n1" for v in many})[3]["state"] == "unresponsive"
    small = {1: dict(BAD), 2: dict(BAD), 3: dict(BAD)}  # only 3 VMs: not enough to call it a host problem
    assert suppress_node_wide_failures(small, {v: "n1" for v in small})[1]["state"] == "unresponsive"
    two_nodes = {1: dict(BAD), 2: dict(BAD), 3: dict(BAD), 4: dict(BAD), 5: dict(OK), 6: dict(OK), 7: dict(OK), 8: dict(OK)}
    out = suppress_node_wide_failures(two_nodes, {1: "a", 2: "a", 3: "a", 4: "a", 5: "b", 6: "b", 7: "b", 8: "b"})
    assert out[1]["state"] == "unknown" and out[5]["state"] == "ok"  # judged per host
