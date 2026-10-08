"""Balance Load preview: projected per-node memory after the plan's own moves. Pure logic: no database."""

from pyxie_core.balance_workflow import projected_memory_rows

GB = 1024 ** 3
NODES = [
    {"node_id": "a", "name": "node-a", "mem_total_bytes": 100 * GB, "mem_usage_pct": 90},
    {"node_id": "b", "name": "node-b", "mem_total_bytes": 100 * GB, "mem_usage_pct": 30},
]


def test_moves_debit_the_source_and_credit_the_destination():
    plan = [{"source_node_id": "a", "destination_node_id": "b", "memory_bytes": 20 * GB, "transport": "live"}]
    rows = {r["node_id"]: r for r in projected_memory_rows(NODES, plan)}
    assert (rows["a"]["before_pct"], rows["a"]["after_pct"]) == (90.0, 70.0)
    assert (rows["b"]["before_pct"], rows["b"]["after_pct"]) == (30.0, 50.0)


def test_lines_set_to_dont_move_are_ignored():
    plan = [{"source_node_id": "a", "destination_node_id": "b", "memory_bytes": 20 * GB, "transport": "skip"}]
    assert [r["after_pct"] for r in projected_memory_rows(NODES, plan)] == [90.0, 30.0]


def test_empty_plan_changes_nothing_and_never_exceeds_100():
    assert [r["after_pct"] for r in projected_memory_rows(NODES, [])] == [90.0, 30.0]
    big = [{"source_node_id": "a", "destination_node_id": "b", "memory_bytes": 200 * GB}]
    assert max(r["after_pct"] for r in projected_memory_rows(NODES, big)) == 100.0
