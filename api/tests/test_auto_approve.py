"""Auto-approve safety gates and its confirmation rules. Pure logic: no database."""

import pytest

from pyxie_core.auto_balance import AUTO_APPROVE_MOVES_PER_RUN, auto_approve_blockers
from pyxie_core.balance_config import ConfigError, validate_config

MOVE = {"workload_id": "w", "transport": "live", "destination_storage_id": None}
OK = [{"name": "n1", "after_pct": 60.0}, {"name": "n2", "after_pct": 55.0}]


def blockers(plan=None, projected=OK, writes=True, busy=False):
    return auto_approve_blockers([MOVE] if plan is None else plan, projected, writes_enabled=writes, busy_nodes=busy)


def test_one_move_at_a_time():
    assert AUTO_APPROVE_MOVES_PER_RUN == 1


def test_a_clean_live_move_may_be_approved():
    assert blockers() == []


def test_write_switch_off_leaves_the_plan_for_a_person():
    assert any("write switch" in r for r in blockers(writes=False))


def test_storage_changes_and_non_live_moves_are_left_for_a_person():
    assert any("storage" in r for r in blockers(plan=[{**MOVE, "destination_storage_id": "s1"}]))
    assert any("live migration" in r for r in blockers(plan=[{**MOVE, "transport": "offline"}]))


def test_a_move_that_leaves_a_node_over_the_ceiling_is_left_for_a_person():
    assert any("80% memory" in r for r in blockers(projected=[{"name": "n3", "after_pct": 81.0}]))
    assert blockers(projected=[{"name": "n3", "after_pct": 80.0}]) == []


def test_busy_nodes_and_empty_plans_are_not_approved():
    assert any("busy" in r for r in blockers(busy=True))
    assert any("no moves" in r for r in blockers(plan=[]))


def test_every_reason_is_reported_not_just_the_first():
    assert len(blockers(writes=False, busy=True, plan=[{**MOVE, "destination_storage_id": "s"}])) == 3


def ack(aggressive=False):
    return {"by": "phil@example.com", "at": "2026-10-08T12:00:00+00:00", "aggressive": aggressive}


def test_auto_approve_cannot_be_saved_without_the_confirmation():
    with pytest.raises(ConfigError):
        validate_config({"mode": "auto_approve", "level": "conservative"})
    with pytest.raises(ConfigError):
        validate_config({"mode": "auto_approve", "level": "conservative", "ack": {"by": "", "at": ""}})


def test_aggressive_needs_the_second_confirmation():
    with pytest.raises(ConfigError):
        validate_config({"mode": "auto_approve", "level": "aggressive", "ack": ack(False)})
    assert validate_config({"mode": "auto_approve", "level": "aggressive", "ack": ack(True)})["ack"]["aggressive"] is True


def test_the_confirmation_is_kept_only_while_auto_approve_is_on():
    assert validate_config({"mode": "auto_approve", "level": "moderate", "ack": ack()})["ack"]["by"] == "phil@example.com"
    assert validate_config({"mode": "recommend", "level": "moderate", "ack": ack()})["ack"] is None
    assert validate_config({"mode": "off"})["ack"] is None
