"""Regression coverage for exact server-side vmid task filtering
(source="active"), not a client-side substring check on the archive list.
Fully isolated -- takes only
a fake PveClient, no database.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.preconditions import revalidate_live_migration


def _healthy_cluster_status():
    return [
        {"type": "cluster", "quorate": 1},
        {"type": "node", "name": "src", "online": 1},
        {"type": "node", "name": "dst", "online": 1},
    ]


def _fake_client(*, active_tasks):
    client = MagicMock()
    client.cluster_status = MagicMock(return_value=_healthy_cluster_status())
    client.qemu_list = MagicMock(return_value=[{"vmid": 100, "status": "running"}])
    client.node_status = MagicMock(return_value={"memory": {"total": 10_000_000_000, "used": 1_000_000_000}})
    client.tasks = MagicMock(return_value=active_tasks)
    return client


def test_active_task_on_this_vm_blocks():
    """The old check called PVE's default task-list source ('archive',
    finished-only) and looked for status=='running', which never appears
    in an archive listing -- so an actually in-progress task was never
    detected. Fixed to request source='active' + vmid=<vmid> (PVE's own
    documented server-side filters, confirmed via
    `pvesh usage /nodes/{node}/tasks`)."""
    client = _fake_client(active_tasks=[{"upid": "UPID:pve-node-1:0:0:0:qmigrate:100:root@pam:", "id": "100"}])
    result = revalidate_live_migration(
        client, source_node="src", vmid=100, target_node="dst",
        expected_memory_bytes=None, require_running=True,
    )
    assert result.ok is False
    assert any("active PVE task" in r for r in result.reasons)


def test_no_active_task_does_not_block():
    client = _fake_client(active_tasks=[])
    result = revalidate_live_migration(
        client, source_node="src", vmid=100, target_node="dst",
        expected_memory_bytes=None, require_running=True,
    )
    assert result.ok is True


def test_task_query_uses_exact_server_side_vmid_filter_not_a_substring_check():
    """Guards against reintroducing the 'archive + client-side substring'
    pattern, which false-positived on e.g. vmid 100 matching inside a
    task belonging to vmid 1100. The fix relies on PVE's own exact vmid=
    filter instead of re-deriving membership from a raw id string."""
    client = _fake_client(active_tasks=[])
    revalidate_live_migration(
        client, source_node="src", vmid=100, target_node="dst",
        expected_memory_bytes=None, require_running=True,
    )
    client.tasks.assert_called_once_with("src", limit=50, source="active", vmid=100)
