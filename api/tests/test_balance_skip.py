"""Balance Load / Bulk Migrate: lines the operator sets to "Don't move" must not run.

Pure logic: no database."""

from pyxie_core.balance_workflow import plan_items_to_move


def test_skipped_lines_are_dropped_and_others_kept_in_order():
    plan = [
        {"workload_id": "a", "transport": "live"},
        {"workload_id": "b", "transport": "skip"},
        {"workload_id": "c", "transport": "offline"},
        {"workload_id": "d"},
    ]
    assert [i["workload_id"] for i in plan_items_to_move(plan)] == ["a", "c", "d"]


def test_everything_skipped_leaves_nothing_to_run():
    assert plan_items_to_move([{"workload_id": "a", "transport": "skip"}]) == []
