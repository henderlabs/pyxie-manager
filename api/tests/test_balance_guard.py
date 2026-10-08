"""Balance Load planner guard: never swap the imbalance. Pure logic: no database."""

from pyxie_core.balance_workflow import drop_unhelpful_moves

GB = 1024 ** 3
# The lab shape that exposed it: two big busy nodes, two small quiet ones.
NODES = [
    {"node_id": "big", "name": "st-big", "mem_total_bytes": 64 * GB, "mem_usage_pct": 62},
    {"node_id": "small", "name": "st-small", "mem_total_bytes": 32 * GB, "mem_usage_pct": 30},
    {"node_id": "mid", "name": "st-mid", "mem_total_bytes": 128 * GB, "mem_usage_pct": 40},
]


def move(src, dst, gb, wid="w1"):
    return {"workload_id": wid, "vmid": 100, "name": wid, "memory_bytes": gb * GB, "source_node_id": src,
            "destination_node_id": dst, "improvement": 20.0}


def test_move_that_makes_the_destination_fuller_than_the_source_is_dropped():
    kept, dropped = drop_unhelpful_moves(NODES, [move("big", "small", 16)])  # small: 30% -> 80%, big is 62%
    assert kept == []
    assert dropped[0]["kind"] == "no_gain" and dropped[0]["blocked_node"] == "st-small"
    assert "80%" in dropped[0]["blocking_reasons"][0] and "62%" in dropped[0]["blocking_reasons"][0]


def test_move_to_a_roomy_quiet_node_is_kept():
    kept, dropped = drop_unhelpful_moves(NODES, [move("big", "mid", 16)])  # mid: 40% -> 52%
    assert len(kept) == 1 and dropped == []


def test_earlier_kept_moves_count_for_later_ones():
    plan = [move("big", "mid", 20, "a"), move("big", "mid", 20, "b")]  # first: mid 40->56, big 62->31; second: mid 56->72, above big
    kept, dropped = drop_unhelpful_moves(NODES, plan)
    assert [m["workload_id"] for m in kept] == ["a"] and [d["workload_id"] for d in dropped] == ["b"]


def test_unknown_nodes_or_sizes_are_left_alone():
    plan = [move("gone", "mid", 8), {**move("big", "mid", 0), "memory_bytes": None}]
    kept, dropped = drop_unhelpful_moves(NODES, plan)
    assert len(kept) == 2 and dropped == []
