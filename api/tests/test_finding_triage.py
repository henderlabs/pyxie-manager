"""findings._reconcile: acknowledged/dismissed end on a new occurrence or a worse severity, and survive a plain
re-observation. In-memory SQLite, same setup as test_notification_rules."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from pyxie_core import findings
from pyxie_core.models import AppSettings, Finding, Notification, NotificationRule, User

T0 = datetime(2026, 10, 7, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(*_a, **_k):
    return "JSON"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    for t in (Finding, Notification, NotificationRule, AppSettings, User):
        t.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.add(AppSettings(id=1, notification_hold_down_minutes=0))
    session.commit()
    yield session
    session.close()


def _f(severity="warning"):
    return {"dedupe_key": "k1", "category": "node", "severity": severity, "title": "t"}


def _row(db):
    return db.query(Finding).filter(Finding.dedupe_key == "k1").one()


def _mark(db, kind):
    r = _row(db)
    setattr(r, f"{kind}_at", T0)
    setattr(r, f"{kind}_by", "phil@x")
    db.commit()


@pytest.mark.parametrize("kind,state", [("acknowledged", "acknowledged"), ("dismissed", "dismissed")])
def test_still_active_keeps_triage(db, kind, state):
    findings._reconcile(db, [_f()], T0)
    _mark(db, kind)
    findings._reconcile(db, [_f()], T0 + timedelta(minutes=5))
    assert findings.triage_state(_row(db)) == state


@pytest.mark.parametrize("kind", ["acknowledged", "dismissed"])
def test_recurrence_clears_triage(db, kind):
    findings._reconcile(db, [_f()], T0)
    _mark(db, kind)
    findings._reconcile(db, [], T0 + timedelta(minutes=5))  # resolves
    assert _row(db).active is False
    findings._reconcile(db, [_f()], T0 + timedelta(minutes=10))  # comes back
    r = _row(db)
    assert r.active is True and findings.triage_state(r) == "open"
    assert r.acknowledged_by is None and r.dismissed_by is None


@pytest.mark.parametrize("kind", ["acknowledged", "dismissed"])
def test_worse_severity_clears_triage(db, kind):
    findings._reconcile(db, [_f("warning")], T0)
    _mark(db, kind)
    findings._reconcile(db, [_f("critical")], T0 + timedelta(minutes=5))
    assert findings.triage_state(_row(db)) == "open"


def test_better_severity_keeps_triage(db):
    findings._reconcile(db, [_f("critical")], T0)
    _mark(db, "dismissed")
    findings._reconcile(db, [_f("warning")], T0 + timedelta(minutes=5))
    assert findings.triage_state(_row(db)) == "dismissed"
