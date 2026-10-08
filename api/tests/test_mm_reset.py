"""Maintenance-mode moves do not count toward the loop breaker. Pure logic: the SQL itself is checked against the lab database."""

from datetime import datetime, timedelta, timezone

from pyxie_core.auto_balance import MAINTENANCE_PARENT_TYPES, flapping_guests

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def test_every_operation_that_empties_a_node_is_excluded():
    assert {"node.evacuate", "node.enter_maintenance", "maintenance.run"} <= set(MAINTENANCE_PARENT_TYPES)
    assert "cluster.rebalance" not in MAINTENANCE_PARENT_TYPES  # balancing moves must still count


def test_after_maintenance_a_guest_may_go_straight_back():
    # The query hands flapping_guests only balancing moves, so an evacuation leaves no history at all.
    settled, avoid = flapping_guests([], NOW)
    assert settled == {} and avoid == {}
    # ...whereas a balancing move 10 h ago still blocks the way back.
    _, avoid = flapping_guests([("g", NOW - timedelta(hours=10), "A")], NOW)
    assert avoid == {"g": {"A"}}
