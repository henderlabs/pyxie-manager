"""recommendations._upsert: an acknowledged/dismissed suggestion keeps its state while unchanged and reopens when the
suggested size changes, the severity rises, or it resolves and comes back. In-memory SQLite."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from pyxie_core import recommendations as R
from pyxie_core.models import Recommendation

T0 = datetime(2026, 10, 7, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(*_a, **_k):
    return "JSON"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Recommendation.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _rec(cpu=2, mem=4, severity="info", category="rightsizing"):
    return {"dedupe_key": "k", "category": category, "title": "t", "severity": severity,
            "evidence": {"cpu_suggestion": {"suggested": cpu}, "memory_suggestion": {"suggested_bytes": mem}}}


def _row(db):
    return db.query(Recommendation).one()


def _triage(db, action, category="rightsizing"):
    R._upsert(db, _rec(category=category))
    db.commit()
    R.apply_triage(_row(db), action, "phil@x", T0)
    db.commit()


def test_fingerprint_is_the_suggested_size():
    assert R.triage_fingerprint("rightsizing", _rec(2, 4)["evidence"]) == "cpu=2;mem=4"
    assert R.triage_fingerprint("updates", {"x": 1}) is None


@pytest.mark.parametrize("action,state", [("acknowledge", "acknowledged"), ("dismiss", "dismissed")])
def test_unchanged_keeps_state(db, action, state):
    _triage(db, action)
    R._upsert(db, _rec())
    db.commit()
    assert _row(db).lifecycle_state == state


@pytest.mark.parametrize("action", ["acknowledge", "dismiss"])
def test_changed_suggestion_reopens(db, action):
    _triage(db, action)
    R._upsert(db, _rec(cpu=1))
    db.commit()
    r = _row(db)
    assert r.lifecycle_state == "open" and r.acknowledged_by is None and r.dismissed_by is None and r.triage_fingerprint is None


@pytest.mark.parametrize("action", ["acknowledge", "dismiss"])
def test_worse_severity_reopens(db, action):
    _triage(db, action)
    R._upsert(db, _rec(severity="warning"))
    db.commit()
    assert _row(db).lifecycle_state == "open"


def test_resolved_then_back_is_fresh(db):
    _triage(db, "dismiss")
    r = _row(db)
    r.lifecycle_state = "resolved"
    R.clear_triage(r)
    db.commit()
    R._upsert(db, _rec())
    db.commit()
    assert _row(db).lifecycle_state == "open"


def test_other_category_has_no_fingerprint_so_size_changes_do_not_reopen(db):
    _triage(db, "dismiss", category="updates")
    R._upsert(db, _rec(cpu=9, category="updates"))
    db.commit()
    assert _row(db).lifecycle_state == "dismissed"
