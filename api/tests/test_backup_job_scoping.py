"""Regression coverage for node/pool-scoped PBS backup job membership
computation. Fully isolated -- db and the
PVE client are faked; nothing here can reach a real database or PVE
cluster. See tests/fakes.py for why that matters.
"""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import fake_pve_client, make_query_router

from pyxie_core import protection_workflow as pw


def _setup():
    cluster = SimpleNamespace(id=uuid.uuid4(), pve_target_id=uuid.uuid4())
    target = SimpleNamespace(id=cluster.pve_target_id)
    storage = SimpleNamespace(name="pbs-backup", type="pbs", cluster_id=cluster.id)
    node_a = SimpleNamespace(name="pve-node-1")
    node_b = SimpleNamespace(name="pve-node-2")
    wl_on_a = SimpleNamespace(id=uuid.uuid4(), vmid=100, name="on-node-a", node=node_a, is_missing=False)
    wl_on_b = SimpleNamespace(id=uuid.uuid4(), vmid=200, name="on-node-b", node=node_b, is_missing=False)
    db = MagicMock()
    db.query.side_effect = make_query_router({
        pw.Cluster: [cluster], pw.PveTarget: [target], pw.Storage: [storage],
        pw.Workload: [wl_on_a, wl_on_b],
    })
    return db, cluster, wl_on_a, wl_on_b, node_a


def test_node_scoped_all_guests_job_excludes_off_node_vm():
    """PVE's own semantics: vzdump backs up whichever guests are LOCAL to
    whichever node actually runs it, so a node-scoped all=1 job only
    covers that node's guests. This used to be treated as 'every known
    workload,' incorrectly reporting an off-node VM as included."""
    db, cluster, wl_on_a, wl_on_b, node_a = _setup()
    job = {"id": "scoped", "storage": "pbs-backup", "all": 1, "node": node_a.name, "schedule": None, "enabled": 1}
    with patch.object(pw, "build_pve_client", return_value=(fake_pve_client([job]), None)):
        jobs = pw.list_pbs_backup_jobs(db, cluster)
    result = next(j for j in jobs if j["job_id"] == "scoped")
    members = {m["workload_id"]: m["included"] for m in result["members"]}
    assert members[str(wl_on_a.id)] is True, "on-scope-node VM should be included"
    assert members[str(wl_on_b.id)] is False, "off-node VM should NOT be included"


def test_unscoped_all_guests_job_still_covers_everyone():
    """An all=1 job with no node/pool restriction is unchanged behavior --
    every known workload is covered, matching the real live nightly-backup
    job's own current (intentional) state."""
    db, cluster, wl_on_a, wl_on_b, node_a = _setup()
    job = {"id": "unscoped", "storage": "pbs-backup", "all": 1, "schedule": None, "enabled": 1}
    with patch.object(pw, "build_pve_client", return_value=(fake_pve_client([job]), None)):
        jobs = pw.list_pbs_backup_jobs(db, cluster)
    result = next(j for j in jobs if j["job_id"] == "unscoped")
    assert all(m["included"] for m in result["members"])


def test_pool_scoped_job_marked_unsupported_not_guessed():
    """PyXie doesn't track PVE resource pools at all yet, so a
    pool-scoped job's real membership can't be computed -- it must be
    surfaced as unsupported, never silently guessed."""
    db, cluster, wl_on_a, wl_on_b, node_a = _setup()
    job = {"id": "pooled", "storage": "pbs-backup", "all": 1, "pool": "some-pool", "schedule": None, "enabled": 1}
    with patch.object(pw, "build_pve_client", return_value=(fake_pve_client([job]), None)):
        jobs = pw.list_pbs_backup_jobs(db, cluster)
    result = next(j for j in jobs if j["job_id"] == "pooled")
    assert result["scope_supported"] is False
    assert all(not m["included"] for m in result["members"])


def test_dry_run_refuses_pool_scoped_job():
    db, cluster, wl_on_a, wl_on_b, node_a = _setup()
    job = {"id": "pooled", "storage": "pbs-backup", "all": 1, "pool": "some-pool", "schedule": None, "enabled": 1}
    with patch.object(pw, "build_pve_client", return_value=(fake_pve_client([job]), None)):
        try:
            pw.dry_run_backup_membership(db, cluster, "pooled", select_all=True, actor="test")
            assert False, "expected ProtectionWorkflowError for a pool-scoped job"
        except pw.ProtectionWorkflowError:
            pass
