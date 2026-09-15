"""Regression coverage for live quorum-after-removal checking against
PVE's own /cluster/status, not a snapshot of PyXie's own Node table.
Fully isolated -- no real
database or network connection; build_pve_client is patched out entirely,
so the db/cluster/target arguments are never actually dereferenced.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core import maintenance


def _fake_client(status):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.cluster_status = MagicMock(return_value=status)
    return client


def test_quorum_holds_when_cluster_healthy():
    status = [
        {"type": "cluster", "quorate": 1},
        {"type": "node", "name": "a", "online": 1},
        {"type": "node", "name": "b", "online": 1},
        {"type": "node", "name": "c", "online": 1},
        {"type": "node", "name": "d", "online": 1},
    ]
    with patch.object(maintenance, "build_pve_client", return_value=(_fake_client(status), None)):
        result = maintenance._quorum_after_removal(None, None, None, "c")
    assert result["evaluated"] is True
    assert result["quorum_holds"] is True
    assert result["remaining_after_removal"] == 3


def test_quorum_fails_when_a_node_is_already_offline():
    """4 configured nodes, one (d) already offline right now; removing a
    SECOND one (c) used to still report quorum_holds=true, because the
    old implementation counted configured-but-not-missing Node rows and
    never checked which of them were actually online."""
    status = [
        {"type": "cluster", "quorate": 1},
        {"type": "node", "name": "a", "online": 1},
        {"type": "node", "name": "b", "online": 1},
        {"type": "node", "name": "c", "online": 1},
        {"type": "node", "name": "d", "online": 0},
    ]
    with patch.object(maintenance, "build_pve_client", return_value=(_fake_client(status), None)):
        result = maintenance._quorum_after_removal(None, None, None, "c")
    assert result["evaluated"] is True
    assert result["quorum_holds"] is False
    assert result["remaining_after_removal"] == 2


def test_quorum_fails_when_cluster_already_not_quorate():
    status = [
        {"type": "cluster", "quorate": 0},
        {"type": "node", "name": "a", "online": 1},
        {"type": "node", "name": "b", "online": 1},
    ]
    with patch.object(maintenance, "build_pve_client", return_value=(_fake_client(status), None)):
        result = maintenance._quorum_after_removal(None, None, None, "b")
    assert result["quorum_holds"] is False


def test_single_node_cluster_not_evaluated():
    status = [{"type": "cluster", "quorate": 1}, {"type": "node", "name": "a", "online": 1}]
    with patch.object(maintenance, "build_pve_client", return_value=(_fake_client(status), None)):
        result = maintenance._quorum_after_removal(None, None, None, "a")
    assert result["evaluated"] is False
