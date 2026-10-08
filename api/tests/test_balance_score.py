"""The cluster balance score is size-aware. Pure logic (no database)."""

from types import SimpleNamespace

from pyxie_core.balance_config import balance_score

GB = 1024 ** 3


def node(mem_pct, size_gb, cpu=0, status="online", maint=False):
    return SimpleNamespace(status=status, maintenance_mode=maint, mem_usage_pct=mem_pct, cpu_usage_pct=cpu, mem_total_bytes=size_gb * GB)


def test_two_equal_nodes_keep_the_old_gap_meaning():
    assert round(balance_score([node(90, 64), node(10, 64)])) == 20  # old score: 100 - 80


def test_small_quiet_node_among_large_ones_counts_for_little():
    lab = [node(62, 64), node(63, 64), node(30, 32), node(50, 32)]
    old = 100 - (63 - 30)
    new = balance_score(lab)
    assert old == 67 and new > old + 5  # still reads below 100, but not as harshly
    assert 70 <= new < 80


def test_a_lone_hot_node_among_equal_ones_still_shows():
    assert balance_score([node(90, 64), node(50, 64), node(50, 64), node(50, 64)]) < 70


def test_even_cluster_scores_100_and_fewer_than_two_nodes_is_none():
    assert balance_score([node(50, 64), node(50, 32)]) == 100
    assert balance_score([node(50, 64)]) is None
    assert balance_score([node(50, 64), node(10, 64, maint=True)]) is None


def test_missing_sizes_fall_back_to_equal_weights():
    a, b = node(90, 64), node(10, 64)
    a.mem_total_bytes = None
    assert round(balance_score([a, b])) == 20


def test_cpu_only_counts_once_the_busiest_is_past_half():
    assert balance_score([node(50, 64, cpu=40), node(50, 64, cpu=0)]) == 100
    assert balance_score([node(50, 64, cpu=80), node(50, 64, cpu=0)]) < 70
