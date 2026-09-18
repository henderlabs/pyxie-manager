"""Rule matching and recipient resolution for email alerts, plus
findings._reconcile's notify-on-begin / notify-on-recovery behavior.
In-memory SQLite with only the tables involved; email send is patched out."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from pyxie_core import findings, notifications
from pyxie_core.models import AppSettings, Finding, Notification, NotificationRule, User

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(*_a, **_k):
    return "JSON"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for t in (Finding, Notification, NotificationRule, AppSettings, User):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(AppSettings(id=1, smtp_enabled=True, smtp_host="h", smtp_from_address="f@x.com", notification_hold_down_minutes=0))
    session.commit()
    yield session
    session.close()


def _rule(db, **kw):
    kw.setdefault("name", "r")
    kw.setdefault("recipients", ["a@x.com"])
    rule = NotificationRule(
        enabled=kw.pop("enabled", True),
        categories=kw.pop("categories", []),
        min_severity=kw.pop("min_severity", "critical"),
        send_recovery=kw.pop("send_recovery", True),
        include_admins=kw.pop("include_admins", False),
        **kw,
    )
    db.add(rule)
    db.commit()
    return rule


def _dispatch(db, **kw):
    args = dict(severity="critical", category="quorum", title="T", recovered=False, observed_at=NOW)
    args.update(kw)
    with patch.object(notifications, "send_email") as mail:
        n = notifications.dispatch_event(db, **args)
    return n, [c.args[1] for c in mail.call_args_list]


def test_severity_threshold(db):
    _rule(db, min_severity="critical")
    assert _dispatch(db, severity="warning")[0] == 0
    assert _dispatch(db, severity="critical")[0] == 1
    db.query(NotificationRule).delete()
    _rule(db, min_severity="warning")
    assert _dispatch(db, severity="warning")[0] == 1


def test_category_filter_and_empty_means_all(db):
    _rule(db, categories=["quorum"])
    assert _dispatch(db, category="node")[0] == 0
    assert _dispatch(db, category="quorum")[0] == 1
    db.query(NotificationRule).delete()
    _rule(db, categories=[])
    assert _dispatch(db, category="storage")[0] == 1


def test_recovery_flag_and_disabled_rule(db):
    _rule(db, send_recovery=False)
    assert _dispatch(db, recovered=True)[0] == 0
    db.query(NotificationRule).delete()
    _rule(db, enabled=False)
    assert _dispatch(db)[0] == 0


def test_overlapping_rules_email_each_address_once(db):
    _rule(db, name="one", recipients=["a@x.com", "b@x.com"])
    _rule(db, name="two", recipients=["A@x.com"])
    n, to = _dispatch(db)
    assert n == 2 and sorted(t.lower() for t in to) == ["a@x.com", "b@x.com"]


def test_include_admins_uses_only_active_admins(db):
    db.add_all(
        [
            User(email="admin@x.com", is_admin=True, is_active=True),
            User(email="gone@x.com", is_admin=True, is_active=False),
            User(email="viewer@x.com", is_admin=False, is_active=True),
        ]
    )
    db.commit()
    _rule(db, recipients=[], include_admins=True)
    assert _dispatch(db)[1] == ["admin@x.com"]


def test_no_email_when_smtp_disabled(db):
    _rule(db)
    db.query(AppSettings).one().smtp_enabled = False
    db.commit()
    assert _dispatch(db)[0] == 0


def _f(key="cluster.not_quorate:1", severity="critical", category="quorum", title="Cluster 'A' is not quorate"):
    return {"dedupe_key": key, "object_type": "cluster", "category": category, "severity": severity, "title": title}


def test_reconcile_notifies_on_start_recurrence_and_recovery(db):
    _rule(db)
    with patch.object(notifications, "send_email") as mail:
        findings._reconcile(db, [_f()])
        findings._reconcile(db, [_f()])  # still active: no repeat
        findings._reconcile(db, [])  # recovery
        findings._reconcile(db, [_f()])  # recurrence
    titles = [n.title for n in db.query(Notification).order_by(Notification.created_at).all()]
    assert titles == ["Cluster 'A' is not quorate", "Recovered: Cluster 'A' is not quorate", "Cluster 'A' is not quorate"]
    subjects = [c.args[2] for c in mail.call_args_list]
    assert subjects == [
        "[PyXie] CRITICAL: Cluster 'A' is not quorate",
        "[PyXie] RECOVERED: Cluster 'A' is not quorate",
        "[PyXie] CRITICAL: Cluster 'A' is not quorate",
    ]


def test_reconcile_recovery_uses_original_severity_for_matching(db):
    _rule(db, min_severity="critical")
    with patch.object(notifications, "send_email") as mail:
        findings._reconcile(db, [_f("u:1", "warning", "update", "Node 'x' has 3 pending update(s)")])
        findings._reconcile(db, [])
    mail.assert_not_called()
    assert db.query(Notification).count() == 2  # in-app notifications are independent of rules


def _set_hold_down(db, minutes):
    db.query(AppSettings).one().notification_hold_down_minutes = minutes
    db.commit()


def _subjects(mail):
    return [c.args[2] for c in mail.call_args_list]


def test_flap_shorter_than_hold_down_is_never_announced(db):
    _rule(db)
    _set_hold_down(db, 5)
    t = NOW
    with patch.object(notifications, "send_email") as mail:
        # up/down every minute for 12 minutes: state never holds 5 minutes
        for minute in range(12):
            findings._reconcile(db, [_f()] if minute % 2 == 0 else [], now=t + timedelta(minutes=minute))
    mail.assert_not_called()
    assert db.query(Notification).count() == 0


def test_sustained_outage_announced_once_after_hold_down_then_recovery_after_hold_down(db):
    _rule(db)
    _set_hold_down(db, 5)
    with patch.object(notifications, "send_email") as mail:
        for minute in range(0, 4):
            findings._reconcile(db, [_f()], now=NOW + timedelta(minutes=minute))
        assert mail.call_count == 0  # not yet 5 minutes
        for minute in range(4, 9):
            findings._reconcile(db, [_f()], now=NOW + timedelta(minutes=minute))
        assert _subjects(mail) == ["[PyXie] CRITICAL: Cluster 'A' is not quorate"]  # exactly once
        findings._reconcile(db, [], now=NOW + timedelta(minutes=9))  # clears
        findings._reconcile(db, [_f()], now=NOW + timedelta(minutes=10))  # blips back
        findings._reconcile(db, [], now=NOW + timedelta(minutes=11))  # clears again
        assert mail.call_count == 1  # blip inside hold-down: silence, no recovery yet
        findings._reconcile(db, [], now=NOW + timedelta(minutes=17))  # stayed clear 6 min
    assert _subjects(mail)[-1] == "[PyXie] RECOVERED: Cluster 'A' is not quorate"
    assert mail.call_count == 2


def test_long_standing_backfilled_finding_does_not_realert(db):
    # what migration 0031 leaves for an already-active finding: announced
    db.add(Finding(dedupe_key="k", category="node", severity="critical", title="old", active=True,
                   first_observed=NOW, last_observed=NOW, state_since=NOW, notified_active=True))
    db.commit()
    _rule(db)
    with patch.object(notifications, "send_email") as mail:
        findings._reconcile(db, [_f("k", "critical", "node", "old")], now=NOW + timedelta(hours=1))
    mail.assert_not_called()
