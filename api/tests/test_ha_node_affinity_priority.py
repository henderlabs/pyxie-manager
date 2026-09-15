"""Regression coverage for HA node-affinity rule priority-suffix parsing
(e.g. "nodes=pve-node-2:2"). Fully isolated -- check_crs_affinity
takes only a client + a workload-like object, no database.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.maintenance import check_crs_affinity


def _fake_client(ha_rules, ha_resources=None, ha_groups=None):
    client = MagicMock()
    client.ha_resources = MagicMock(return_value=ha_resources or [])
    client.ha_groups = MagicMock(return_value=ha_groups or [])
    client.ha_rules = MagicMock(return_value=ha_rules)
    return client


def test_priority_qualified_node_is_recognized_as_eligible():
    """PVE's own --nodes syntax is <node>[:<pri>]{,<node>[:<pri>]}*, per
    the official API (pvesh usage /cluster/ha/rules/{rule}). A strict
    rule listing 'pve-node-2:2' used to incorrectly reject pve-node-2
    itself as a candidate, because only the legacy HA-groups path
    stripped the priority suffix -- the HA-rules path didn't."""
    wl = SimpleNamespace(type="vm", vmid=100)
    rules = [{
        "type": "node-affinity", "resources": "vm:100", "nodes": "pve-node-2:2",
        "strict": True, "name": "test-rule",
    }]
    client = _fake_client(rules)
    reasons = check_crs_affinity(client, wl, ["pve-node-2"])
    assert reasons == [], f"expected pve-node-2 to be accepted, got blocking reasons: {reasons}"


def test_priority_qualified_node_still_blocks_when_truly_ineligible():
    wl = SimpleNamespace(type="vm", vmid=100)
    rules = [{
        "type": "node-affinity", "resources": "vm:100", "nodes": "pve-node-2:2",
        "strict": True, "name": "test-rule",
    }]
    client = _fake_client(rules)
    reasons = check_crs_affinity(client, wl, ["pve-node-3"])
    assert reasons, "expected pve-node-3 (not in the rule's node list) to be blocked"


def test_multiple_priority_qualified_nodes():
    wl = SimpleNamespace(type="vm", vmid=100)
    rules = [{
        "type": "node-affinity", "resources": "vm:100", "nodes": "pve-node-1:1,pve-node-2:2",
        "strict": True, "name": "test-rule",
    }]
    client = _fake_client(rules)
    assert check_crs_affinity(client, wl, ["pve-node-2"]) == []
    assert check_crs_affinity(client, wl, ["pve-node-1"]) == []
    assert check_crs_affinity(client, wl, ["pve-node-4"]) != []
