"""Endpoint failover for the PVE API clients, and the endpoint ordering.
Fully isolated: httpx.MockTransport and plain objects, no network, no database.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core import pve_client as pc
from pyxie_core.credentials import EndpointCandidate, annotate_endpoints, order_endpoint_hosts
from pyxie_core.pve_client import PveAuthError, PveClient, PveConnectionError, PveCredentials
from pyxie_core.pve_write_client import PveMaintenanceClient


@pytest.fixture(autouse=True)
def _clean_bad_cache():
    pc.clear_bad_endpoints()
    yield
    pc.clear_bad_endpoints()


def _creds(hosts):
    return PveCredentials(hostname=hosts[0], api_port=8006, token_user="u@pve", token_id="t",
                          token_secret="s", tls_verify=False, fallback_hostnames=list(hosts[1:]))


def _client(hosts, handler, cls=PveClient):
    return cls(_creds(hosts), transport=httpx.MockTransport(handler))


def _ok(request):
    return httpx.Response(200, json={"data": {"host": request.url.host}})


def test_healthy_primary_is_used_and_nothing_fails():
    c = _client(["a", "b"], _ok)
    assert c.version() == {"host": "a"}
    assert c.active_host == "a" and c.preferred_host == "a"
    assert c.unhealthy_endpoints == []


def test_connect_error_fails_over_to_next_member():
    def handler(request):
        if request.url.host == "a":
            raise httpx.ConnectError("refused")
        return _ok(request)

    c = _client(["a", "b", "c"], handler)
    assert c.version() == {"host": "b"}
    assert c.active_host == "b"
    assert c.unhealthy_endpoints == ["a"]
    assert c.version() == {"host": "b"}  # stays on the working member


def test_connect_timeout_also_fails_over():
    def handler(request):
        if request.url.host == "a":
            raise httpx.ConnectTimeout("blackholed")
        return _ok(request)

    assert _client(["a", "b"], handler).version() == {"host": "b"}


def test_all_members_down_raises_one_clear_error():
    def handler(request):
        raise httpx.ConnectError(f"refused {request.url.host}")

    c = _client(["a", "b"], handler)
    with pytest.raises(PveConnectionError) as ei:
        c.version()
    assert "all 2" in str(ei.value) and "a:" in str(ei.value) and "b:" in str(ei.value)
    assert sorted(c.unhealthy_endpoints) == ["a", "b"]


def test_auth_error_does_not_fail_over():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(401, text="no")

    c = _client(["a", "b"], handler)
    with pytest.raises(PveAuthError):
        c.version()
    assert seen == ["a"]


def test_read_timeout_does_not_fail_over():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        raise httpx.ReadTimeout("slow node")

    c = _client(["a", "b"], handler)
    with pytest.raises(PveConnectionError):
        c.version()
    assert set(seen) == {"a"}  # retried on the SAME node by the caller, never moved


def test_recently_failed_member_is_tried_last_by_new_clients():
    def handler(request):
        if request.url.host == "a":
            raise httpx.ConnectError("down")
        return _ok(request)

    _client(["a", "b"], handler).version()  # marks "a" bad
    seen = []

    def handler2(request):
        seen.append(request.url.host)
        return _ok(request)

    c2 = _client(["a", "b"], handler2)
    c2.version()
    assert seen == ["b"]  # "a" was skipped, not retried first
    assert c2.preferred_host == "a" and c2.unhealthy_endpoints == ["a"]


def test_bad_marker_expires(monkeypatch):
    pc.mark_endpoint_bad("a")
    assert pc.is_endpoint_bad("a")
    monkeypatch.setattr(pc.time, "monotonic", lambda: 10**9)
    assert not pc.is_endpoint_bad("a")


def test_maintenance_write_client_fails_over_too():
    def handler(request):
        if request.url.host == "a":
            raise httpx.ConnectError("down")
        return _ok(request)

    c = _client(["a", "b"], handler, cls=PveMaintenanceClient)
    assert c._request("POST", "/nodes/x/status", data={"command": "reboot"}) == {"host": "b"}


# ------------------------------------------------------------ endpoint ordering

def _cands(*specs):
    return [EndpointCandidate(i, n, ip, online) for i, (n, ip, online) in enumerate(specs, 1)]


NODES = _cands(("m401", "10.0.0.1", True), ("m402", "10.0.0.2", True), ("m403", "10.0.0.3", False))


def test_order_primary_first_then_online_then_offline():
    hosts = order_endpoint_hosts(NODES, target_hostname="10.0.0.2")
    assert hosts == ["10.0.0.2", "10.0.0.1", "10.0.0.3"]


def test_order_avoid_node_goes_last_not_dropped():
    hosts = order_endpoint_hosts(NODES, target_hostname="10.0.0.1", avoid_node_id=1)
    assert hosts == ["10.0.0.2", "10.0.0.3", "10.0.0.1"]


def test_order_uses_dns_names_only_when_they_resolve():
    ok = lambda fqdn, ip: fqdn.startswith(("m401", "m403"))
    hosts = order_endpoint_hosts(NODES, target_hostname="m401.lab.example", domain="lab.example", dns_ok=ok)
    assert hosts == ["m401.lab.example", "10.0.0.2", "m403.lab.example"]


def test_order_primary_matched_by_name_and_configured_host_kept_as_last_resort():
    hosts = order_endpoint_hosts(NODES, target_hostname="cluster-vip.lab.example", domain="lab.example")
    assert hosts[:3] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert hosts[-1] == "cluster-vip.lab.example"


def test_order_with_no_known_nodes_is_just_the_configured_host():
    assert order_endpoint_hosts([], target_hostname="10.0.0.9") == ["10.0.0.9"]


# ------------------------------------------------------------ failover selection + display

def _enabled(c, enabled):
    c.enabled = enabled
    return c


def test_excluded_member_is_left_out_of_the_order():
    nodes = [EndpointCandidate(1, "m401", "10.0.0.1", True), _enabled(EndpointCandidate(2, "m402", "10.0.0.2", True), False),
             EndpointCandidate(3, "m403", "10.0.0.3", True)]
    assert order_endpoint_hosts(nodes, target_hostname="10.0.0.1") == ["10.0.0.1", "10.0.0.3"]


def test_preferred_member_leads_among_online_members():
    nodes = [EndpointCandidate(1, "m401", "10.0.0.1", True), EndpointCandidate(2, "m402", "10.0.0.2", True),
             EndpointCandidate(3, "m403", "10.0.0.3", True)]
    hosts = order_endpoint_hosts(nodes, target_hostname="10.0.0.1", preferred_node_id=3)
    assert hosts == ["10.0.0.3", "10.0.0.1", "10.0.0.2"]


def test_preferred_that_is_excluded_or_unknown_falls_back_to_automatic():
    nodes = [EndpointCandidate(1, "m401", "10.0.0.1", True), _enabled(EndpointCandidate(2, "m402", "10.0.0.2", True), False)]
    assert order_endpoint_hosts(nodes, target_hostname="10.0.0.1", preferred_node_id=2) == ["10.0.0.1"]
    assert order_endpoint_hosts(nodes, target_hostname="10.0.0.1", preferred_node_id=99) == ["10.0.0.1"]


def test_every_member_excluded_leaves_only_the_configured_hostname():
    nodes = [_enabled(EndpointCandidate(1, "m401", "10.0.0.1", True), False)]
    assert order_endpoint_hosts(nodes, target_hostname="10.0.0.9") == ["10.0.0.9"]


def test_annotate_marks_state_addressing_preferred_and_excluded():
    nodes = [EndpointCandidate(1, "m401", "10.0.0.1", True, "online"),
             EndpointCandidate(2, "m402", "10.0.0.2", False, "unknown"),
             _enabled(EndpointCandidate(3, "m403", "10.0.0.3", True, "online"), False)]
    hosts = ["m401.lab.example", "10.0.0.2", "cluster-vip.lab.example"]
    rows = annotate_endpoints(hosts, nodes, domain="lab.example", active="m401.lab.example",
                              unhealthy=["10.0.0.2"], preferred_node_id=1)
    assert [r["node"] for r in rows] == ["m401", "m402", "m403"]  # the manual hostname is not a member row
    assert rows[0]["order"] == 1 and rows[0]["addressing"] == "dns" and rows[0]["state"] == "active" and rows[0]["preferred"]
    assert rows[1]["order"] == 2 and rows[1]["addressing"] == "ip" and rows[1]["state"] == "unreachable"
    assert rows[2]["order"] is None and rows[2]["state"] == "excluded" and rows[2]["enabled"] is False
