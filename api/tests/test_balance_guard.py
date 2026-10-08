"""Balance Load planner guard: never swap the imbalance. Pure logic: no database."""

from pyxie_core.balance_config import swaps_imbalance

GB = 1024 ** 3


def test_move_that_makes_the_destination_fuller_than_the_source_swaps_the_imbalance():
    # The lab shape: a 16 GB guest from a 62% node onto a quiet 32 GB node (30%) would leave that node at 80%.
    swaps, after = swaps_imbalance(62, 30, 16 * GB, 32 * GB)
    assert swaps is True and round(after) == 80


def test_move_to_a_roomy_quiet_node_is_fine():
    swaps, after = swaps_imbalance(62, 40, 16 * GB, 128 * GB)
    assert swaps is False and round(after) == 52


def test_landing_exactly_level_with_the_source_is_allowed():
    assert swaps_imbalance(50, 30, 20 * GB, 100 * GB)[0] is False


def test_rule_uses_the_running_percentages_so_earlier_moves_count():
    # After one 20 GB move off a 62% node, the source is at ~31%; a second 20 GB move onto the same node would pass it.
    first = swaps_imbalance(62, 40, 20 * GB, 128 * GB)
    assert first[0] is False
    src_after = 62 - 20 / 64 * 100
    dst_after = first[1]
    assert swaps_imbalance(src_after, dst_after, 20 * GB, 128 * GB)[0] is True
