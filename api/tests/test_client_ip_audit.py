"""Audit events pick up the request-scoped client IP; system events do not.
Isolated: a mock Session, no database.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.audit import write_audit_event
from pyxie_core.request_context import current_client_ip


def _write(**kw):
    db = MagicMock()
    return write_audit_event(db, event_category="auth", event_type="auth.login.success", commit=False, **kw)


def test_client_ip_is_stamped_when_set():
    token = current_client_ip.set("10.8.82.166")
    try:
        ev = _write(actor="a@example.com", actor_type="user")
    finally:
        current_client_ip.reset(token)
    assert ev.event_metadata == {"client_ip": "10.8.82.166"}


def test_existing_metadata_is_preserved_and_not_mutated():
    token = current_client_ip.set("10.8.82.166")
    meta = {"summary": "x"}
    try:
        ev = _write(metadata=meta)
    finally:
        current_client_ip.reset(token)
    assert ev.event_metadata == {"summary": "x", "client_ip": "10.8.82.166"}
    assert meta == {"summary": "x"}


def test_caller_supplied_client_ip_wins():
    token = current_client_ip.set("10.8.82.166")
    try:
        ev = _write(metadata={"client_ip": "1.2.3.4"})
    finally:
        current_client_ip.reset(token)
    assert ev.event_metadata == {"client_ip": "1.2.3.4"}


def test_no_context_means_no_metadata():
    assert current_client_ip.get() is None
    assert _write(actor_type="system").event_metadata is None
