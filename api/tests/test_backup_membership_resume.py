"""Regression coverage for resuming a protection.backup_membership
operation from the "revalidating" stage after a worker crash/restart.
Fully isolated -- db and
every PVE-touching dependency are faked; nothing here can reach a real
database or PVE cluster. See tests/fakes.py for why that matters.
"""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeOperation, fake_block_operation, fake_enter_stage, fake_fail_operation, fake_pve_client, make_query_router

from pyxie_core import protection_workflow as pw


def _build_fixture():
    cluster = SimpleNamespace(id=uuid.uuid4(), pve_target_id=uuid.uuid4())
    target = SimpleNamespace(id=cluster.pve_target_id)
    op = FakeOperation(
        status="revalidating",
        cluster_id=cluster.id,
        context={
            "job_id": "nightly-backup", "storage": "pbs-backup",
            "current_all": True, "current_exclude": [], "current_vmids": [],
            "desired_all": True, "desired_exclude": [], "desired_vmids": [],
        },
    )
    db = MagicMock()
    db.query.side_effect = make_query_router({pw.Operation: [op], pw.Cluster: [cluster], pw.PveTarget: [target]})
    fake_client = fake_pve_client([{"id": "nightly-backup", "storage": "pbs-backup", "all": 1}])
    return db, op, fake_client


def test_resume_from_revalidating_completes_instead_of_unexpected_state():
    """execute_backup_membership only entered its lock/execute block when
    op.status == 'approved' -- a worker crash/restart after the status
    was already persisted as 'revalidating' (but before reaching
    'executing') fell through to a final 'unexpected state' error on
    resume, because nothing else in the function matched. Fixed to
    accept ('approved', 'revalidating') as the entry gate."""
    db, op, fake_client = _build_fixture()
    fake_write_client = MagicMock()
    fake_write_client.__enter__ = MagicMock(return_value=fake_write_client)
    fake_write_client.__exit__ = MagicMock(return_value=False)

    with patch.object(pw, "enter_stage", side_effect=fake_enter_stage), \
         patch.object(pw, "fail_operation", side_effect=fake_fail_operation), \
         patch.object(pw, "block_operation", side_effect=fake_block_operation), \
         patch.object(pw, "acquire_lock", return_value=None), \
         patch.object(pw, "release_locks_for_operation", return_value=None), \
         patch.object(pw, "build_pve_client", return_value=(fake_client, None)), \
         patch.object(pw, "load_pve_credentials", return_value=MagicMock()), \
         patch.object(pw, "PveMaintenanceClient", return_value=fake_write_client):
        result = pw.execute_backup_membership(db, op.id)

    assert not (result.status == "failed" and "unexpected state" in (result.error or "")), (
        f"resume from 'revalidating' hit the unexpected-state fallback -- error: {result.error}"
    )
    assert result.status == "completed"


def test_resume_from_approved_still_works_unchanged():
    """Same fixture, but status='approved' -- the original entry path
    must still work exactly as before; broadening the condition to
    include 'revalidating' must not change 'approved' behavior."""
    db, op, fake_client = _build_fixture()
    op.status = "approved"
    fake_write_client = MagicMock()
    fake_write_client.__enter__ = MagicMock(return_value=fake_write_client)
    fake_write_client.__exit__ = MagicMock(return_value=False)

    with patch.object(pw, "enter_stage", side_effect=fake_enter_stage), \
         patch.object(pw, "fail_operation", side_effect=fake_fail_operation), \
         patch.object(pw, "block_operation", side_effect=fake_block_operation), \
         patch.object(pw, "acquire_lock", return_value=None), \
         patch.object(pw, "release_locks_for_operation", return_value=None), \
         patch.object(pw, "build_pve_client", return_value=(fake_client, None)), \
         patch.object(pw, "load_pve_credentials", return_value=MagicMock()), \
         patch.object(pw, "PveMaintenanceClient", return_value=fake_write_client):
        result = pw.execute_backup_membership(db, op.id)

    assert result.status == "completed"
