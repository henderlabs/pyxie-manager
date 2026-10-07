"""Needs a real Postgres: set TEST_DATABASE_URL (a throwaway database). Skipped otherwise.

Proves the discovery lock is released even when the connection pool rotates connections mid-run, which is what leaked
the lock on the lab (after the first commit the Session gives its connection back; the unlock then ran on a different
one and the real holder kept the lock forever, so every later discovery 'skipped')."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

URL = os.environ.get("TEST_DATABASE_URL")


def _setup():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(URL, pool_size=5, max_overflow=0)
    warm = [eng.connect() for _ in range(4)]  # four idle pooled connections, so checkouts rotate
    for c in warm:
        c.close()
    return eng, sessionmaker(bind=eng), text


def _lock_is_free(text, target_id):
    from sqlalchemy import create_engine

    other = create_engine(URL)  # a different engine = different connections: sees the real lock state
    with other.connect() as c:
        got = c.execute(text("SELECT pg_try_advisory_lock(hashtext('pyxie:discovery'), hashtext(:k))"), {"k": target_id}).scalar()
        if got:
            c.execute(text("SELECT pg_advisory_unlock(hashtext('pyxie:discovery'), hashtext(:k))"), {"k": target_id})
        c.commit()
    other.dispose()
    return bool(got)


def test_lock_is_released_after_a_run_that_commits_midway(monkeypatch=None):
    if not URL:
        return
    from pyxie_core import discovery

    eng, Session, text = _setup()
    target = SimpleNamespace(id="lock-test-1")
    db = Session()
    discovery._run_discovery_inner = lambda db_, t, a: (db_.commit(), {"status": "ok"})[1]  # commits like the real one
    assert discovery.run_discovery(db, target, "test") == {"status": "ok"}
    db.close()
    assert _lock_is_free(text, "lock-test-1"), "discovery lock leaked"


def test_lock_is_released_when_the_run_raises():
    if not URL:
        return
    from pyxie_core import discovery

    eng, Session, text = _setup()
    target = SimpleNamespace(id="lock-test-2")
    db = Session()

    def boom(db_, t, a):
        db_.commit()
        raise RuntimeError("PVE exploded")

    discovery._run_discovery_inner = boom
    try:
        discovery.run_discovery(db, target, "test")
    except RuntimeError:
        pass
    db.close()
    assert _lock_is_free(text, "lock-test-2"), "discovery lock leaked after an error"


def test_second_caller_skips_while_first_holds_the_lock():
    if not URL:
        return
    from pyxie_core import discovery

    eng, Session, text = _setup()
    target = SimpleNamespace(id="lock-test-3")
    seen = {}

    def inner(db_, t, a):
        db_.commit()
        seen["second"] = discovery.run_discovery(Session(), target, "second")  # while the first still holds it
        return {"status": "ok"}

    discovery._run_discovery_inner = inner
    db = Session()
    assert discovery.run_discovery(db, target, "first") == {"status": "ok"}
    db.close()
    assert seen["second"]["status"] == "skipped"
    assert _lock_is_free(text, "lock-test-3")
