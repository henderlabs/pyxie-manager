"""Automatic balancing settings and gates. Pure logic: no database."""

from datetime import datetime
from types import SimpleNamespace as N

import pytest

from pyxie_core.balance_config import ConfigError, PRESETS, balance_score, in_window, resolve_metric, validate_config


def node(mem, cpu, status="online", maint=False):
    return N(mem_usage_pct=mem, cpu_usage_pct=cpu, status=status, maintenance_mode=maint)


def test_levels_get_stricter_toward_conservative():
    c, m, a = PRESETS["conservative"], PRESETS["moderate"], PRESETS["aggressive"]
    assert c["trigger_score"] < m["trigger_score"] < a["trigger_score"]
    assert c["min_benefit"] > m["min_benefit"] > a["min_benefit"]
    assert c["max_moves"] < m["max_moves"] < a["max_moves"]
    assert c["cluster_cooldown_hours"] > m["cluster_cooldown_hours"] > a["cluster_cooldown_hours"]


def test_most_limited_picks_the_hotter_resource_and_memory_wins_ties():
    assert resolve_metric("most_limited", [node(60, 90), node(40, 20)]) == "cpu"
    assert resolve_metric("most_limited", [node(80, 50), node(40, 20)]) == "memory"
    assert resolve_metric("most_limited", [node(70, 70)]) == "memory"
    assert resolve_metric("most_limited", [node(70, None)]) == "memory"
    assert resolve_metric("cpu", [node(99, 1)]) == "cpu"
    assert resolve_metric("both", [node(99, 1)]) == "both"


def test_offline_and_maintenance_nodes_do_not_count():
    assert resolve_metric("most_limited", [node(50, 95, maint=True), node(60, 30)]) == "memory"
    assert balance_score([node(82, 10), node(35, 5), node(20, 1, status="offline")]) == 53
    assert balance_score([node(50, 5)]) is None


def test_balance_score_counts_cpu_only_past_50():
    assert balance_score([node(50, 10), node(48, 40)]) == 98  # cpu gap ignored while the busiest cpu < 50
    assert balance_score([node(50, 20), node(48, 80)]) == 40


def test_windows():
    mon_22 = datetime(2026, 10, 5, 22, 30)  # a Monday
    tue_02 = datetime(2026, 10, 6, 2, 0)
    assert in_window([], mon_22)
    night = [{"days": [0], "start": "22:00", "end": "05:00"}]  # Monday night into Tuesday morning
    assert in_window(night, mon_22) and in_window(night, tue_02)
    assert not in_window(night, datetime(2026, 10, 6, 12, 0))
    assert not in_window(night, datetime(2026, 10, 7, 2, 0))  # Wednesday 02:00: Tuesday night was not selected
    day = [{"days": [0, 1], "start": "09:00", "end": "17:00"}]
    assert in_window(day, datetime(2026, 10, 6, 9, 0)) and not in_window(day, datetime(2026, 10, 6, 17, 0))


def test_validation_rejects_phase_two_and_bad_input():
    assert validate_config({"mode": "recommend"})["level"] == "moderate"
    with pytest.raises(ConfigError):
        validate_config({"mode": "auto_approve"})
    with pytest.raises(ConfigError):
        validate_config({"level": "reckless"})
    with pytest.raises(ConfigError):
        validate_config({"windows": [{"days": [], "start": "22:00", "end": "05:00"}]})
    with pytest.raises(ConfigError):
        validate_config({"windows": [{"days": [1], "start": "25:00", "end": "05:00"}]})
    ok = validate_config({"mode": "recommend", "windows": [{"days": [4, 0, 4], "start": "22:00", "end": "05:00"}]})
    assert ok["windows"][0]["days"] == [0, 4]
