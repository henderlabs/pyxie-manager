"""The automatic-balancing "plan ready" event must be matchable by a notification rule. Pure logic."""

from types import SimpleNamespace

from pyxie_core.notifications import CATEGORY_KEYS, rule_matches


def rule(min_severity, categories):
    return SimpleNamespace(enabled=True, categories=categories, min_severity=min_severity, send_recovery=True)


def test_balance_is_a_selectable_category():
    assert "balance" in CATEGORY_KEYS


def test_a_warning_level_rule_for_balance_matches_the_plan_ready_event():
    assert rule_matches(rule("warning", ["balance"]), category="balance", severity="warning", recovered=False)


def test_default_critical_rules_do_not_email_plan_ready_events():
    assert not rule_matches(rule("critical", []), category="balance", severity="warning", recovered=False)


def test_info_can_never_match_which_is_why_the_event_is_a_warning():
    assert not rule_matches(rule("warning", []), category="balance", severity="info", recovered=False)
