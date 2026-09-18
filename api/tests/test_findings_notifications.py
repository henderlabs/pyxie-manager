"""Notification behavior of findings._reconcile: notify when a condition
begins (including a recurrence after being resolved), notify on recovery,
and email only critical events. Uses an in-memory SQLite DB with just the
two tables involved, and patches out the actual email send."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from pyxie_core import findings
from pyxie_core.models import Finding, Notification


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(*_a, **_k):
    return "JSON"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Finding.__table__.create(engine)
    Notification.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _f(key="cluster.not_quorate:1", severity="critical", title="Cluster 'A' is not quorate"):
    return {"dedupe_key": key, "object_type": "cluster", "category": "quorum", "severity": severity, "title": title}


def _titles(db):
    return [n.title for n in db.query(Notification).order_by(Notification.created_at).all()]


def test_first_sighting_notifies_once_not_every_pass(db):
    with patch.object(findings, "send_alert_email") as mail:
        findings._reconcile(db, [_f()])
        findings._reconcile(db, [_f()])
    assert _titles(db) == ["Cluster 'A' is not quorate"]
    assert mail.call_count == 1


def test_recovery_and_recurrence_each_notify(db):
    with patch.object(findings, "send_alert_email") as mail:
        findings._reconcile(db, [_f()])  # opens
        findings._reconcile(db, [])  # resolves
        findings._reconcile(db, [_f()])  # same key, second outage
    assert _titles(db) == [
        "Cluster 'A' is not quorate",
        "Recovered: Cluster 'A' is not quorate",
        "Cluster 'A' is not quorate",
    ]
    assert mail.call_count == 3
    assert "RECOVERED" in mail.call_args_list[1].args[1]


def test_warnings_notify_in_app_but_are_not_emailed(db):
    w = _f("node.pending_updates:1", "warning", "Node 'x' has 3 pending update(s)")
    with patch.object(findings, "send_alert_email") as mail:
        findings._reconcile(db, [w])
        findings._reconcile(db, [])
    assert len(_titles(db)) == 2
    mail.assert_not_called()
