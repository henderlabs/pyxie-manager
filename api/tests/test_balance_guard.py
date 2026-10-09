"""Balance Load planner guard: never swap the imbalance. Pure logic: no database."""

from pyxie_core.balance_config import MIN_GAP_GAIN, swaps_imbalance

GB = 1024 ** 3


def test_move_that_makes_the_destination_fuller_than_the_source_swaps_the_imbalance():
    # The lab shape: a 16 GB guest from a 62% node onto a quiet 32 GB node (30%) would leave that node at 80%.
    swaps, after = swaps_imbalance(62, 30, 16 * GB, 32 * GB, 64 * GB)
    assert swaps is True and round(after) == 80


def test_a_move_off_a_hot_node_onto_a_roomy_quiet_one_is_fine():
    swaps, after = swaps_imbalance(90, 30, 8 * GB, 128 * GB, 64 * GB)  # 77.5% / 36%: the gap shrinks from 60 to 41
    assert swaps is False and round(after) == 36


def test_equal_size_nodes_that_would_only_trade_places_are_rejected():
    # The first real Aggressive plan on the lab: 10 GB from a 63% node to a 31% node, both 31 GB. They end at 31% and 63%.
    swaps, _ = swaps_imbalance(63, 31, 10 * GB, 31 * GB, 31 * GB)
    assert swaps is True


def test_landing_level_with_the_source_gains_nothing_and_is_rejected():
    assert swaps_imbalance(50, 30, 20 * GB, 100 * GB, 100 * GB)[0] is True


def test_the_gap_must_narrow_by_a_real_margin():
    assert MIN_GAP_GAIN == 5.0
    assert swaps_imbalance(80, 40, 4 * GB, 64 * GB, 64 * GB)[0] is False  # 80->73.75 / 40->46.25: gap 40 -> 27.5
    assert swaps_imbalance(52, 50, 1 * GB, 64 * GB, 64 * GB)[0] is True  # a 2-point gap cannot be narrowed by 5


def test_rule_uses_the_running_percentages_so_earlier_moves_count():
    # A first 8 GB move is fine; a second identical one then no longer narrows the (now smaller) gap enough.
    first = swaps_imbalance(70, 40, 8 * GB, 64 * GB, 64 * GB)
    assert first[0] is False
    src_after = 70 - 8 / 64 * 100
    second = swaps_imbalance(src_after, first[1], 8 * GB, 64 * GB, 64 * GB)
    assert second[0] is True
