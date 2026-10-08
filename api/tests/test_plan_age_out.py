"""Which waiting Balance Load plans are cleared out of the way of automatic balancing. Pure logic."""

from pyxie_core.auto_balance import STALE_PLAN_HOURS, stale_waiting_plan

OLD = STALE_PLAN_HOURS + 1
C1 = "cluster-1"


def test_old_manual_plan_ages_out():
    assert stale_waiting_plan("awaiting_approval", {"node_ids": None}, OLD, C1)
    assert stale_waiting_plan("awaiting_approval", None, OLD, C1)


def test_fresh_manual_plan_keeps_blocking():
    assert not stale_waiting_plan("awaiting_approval", {}, STALE_PLAN_HOURS - 1, C1)


def test_bulk_migrate_never_ages_out():
    assert not stale_waiting_plan("awaiting_approval", {"mode": "bulk_migrate"}, OLD * 10, C1)


def test_only_waiting_plans_age_out_never_running_ones():
    for status in ("approved", "executing", "monitoring", "verifying"):
        assert not stale_waiting_plan(status, {}, OLD, C1)


def test_automatic_plan_ages_out_for_its_own_cluster_only():
    ctx = {"automatic": True, "cluster_id": C1}
    assert stale_waiting_plan("awaiting_approval", ctx, OLD, C1)
    assert not stale_waiting_plan("awaiting_approval", ctx, OLD, "cluster-2")
