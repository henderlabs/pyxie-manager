"""Shared credential-slot loading, used by both the read-only discovery path
(slot 'inventory') and the write-capable operations engine (slot
'maintenance') -- kept in one place so the two never accidentally cross.

Also the single choke point for *which PVE API endpoint* a client talks to.
PyXie was originally hardcoded to always dial PveTarget.hostname (one fixed
cluster member) -- fine for read-only inventory, but a real problem once
PyXie itself performs host reboots (Stage W5/W6): if that one hardcoded
node is the node being rebooted, PyXie loses its own connection to the
ENTIRE cluster for the reboot window, not just to that node.
resolve_pve_endpoint() below fixes that by preferring a healthy, online
cluster member other than the node under maintenance, learned from Node
rows populated by discovery.run_discovery() (see Node.management_ip).
"""

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy.orm import Session

from .crypto import decrypt_secret
from .models import Cluster, HostMaintenanceCredential, Node, PveCredential, PveTarget
from .pve_client import PveCredentials
from .host_maintenance_client import HostMaintenanceCredentials


class CredentialNotConfigured(Exception):
    pass


class HostNotAddressable(Exception):
    """Raised when a node has no management_ip on record yet (run
    discovery first) -- host-maintenance SSH always targets one specific
    node directly, unlike the PVE API's cluster-aware failover."""


@dataclass
class EndpointCandidate:
    node_id: object
    name: str
    ip: str
    online: bool


_DNS_TTL = 300.0
_dns_cache: dict = {}
_dns_lock = threading.Lock()


def _dns_resolves_to(fqdn: str, ip: str) -> bool:
    """True when `fqdn` currently resolves to the node's known management IP.
    Cached for a few minutes -- clients are built constantly."""
    key = (fqdn.lower(), ip)
    now_ = time.monotonic()
    with _dns_lock:
        hit = _dns_cache.get(key)
        if hit and hit[0] > now_:
            return hit[1]
    try:
        ok = ip in {info[4][0] for info in socket.getaddrinfo(fqdn, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        ok = False
    with _dns_lock:
        _dns_cache[key] = (now_ + _DNS_TTL, ok)
    return ok


def _domain_of(hostname: Optional[str]) -> Optional[str]:
    """The DNS suffix of the configured hostname (pve-slc-m401.slc.crengland.com
    -> slc.crengland.com), or None for an IP / a bare short name."""
    if not hostname or "." not in hostname:
        return None
    try:
        ipaddress.ip_address(hostname)
        return None
    except ValueError:
        return hostname.split(".", 1)[1]


def order_endpoint_hosts(
    candidates: list,
    *,
    target_hostname: Optional[str],
    avoid_node_id=None,
    domain: Optional[str] = None,
    dns_ok: Callable[[str, str], bool] = lambda fqdn, ip: False,
) -> list:
    """The ordered list of API endpoints to try (pure function, unit-tested).

    Order: online members first (the configured primary leading, then by
    name), then members last seen not-online (their recorded status can be
    stale -- exactly when failover matters), then the node being avoided
    (e.g. under maintenance), then the configured hostname if it is not itself
    a known node. A member is addressed by DNS name when that name currently
    resolves to its known IP, otherwise by IP."""
    th = (target_hostname or "").lower()

    def host_for(c: EndpointCandidate) -> str:
        if domain:
            fqdn = f"{c.name}.{domain}"
            if dns_ok(fqdn, c.ip):
                return fqdn
        return c.ip

    def is_primary(c: EndpointCandidate) -> bool:
        names = {c.ip.lower(), c.name.lower()}
        if domain:
            names.add(f"{c.name}.{domain}".lower())
        return th in names

    ordered = sorted(candidates, key=lambda c: c.name)
    avoid = [c for c in ordered if avoid_node_id is not None and str(c.node_id) == str(avoid_node_id)]
    rest = [c for c in ordered if c not in avoid]
    online = sorted((c for c in rest if c.online), key=lambda c: 0 if is_primary(c) else 1)
    offline = [c for c in rest if not c.online]

    hosts: list = []
    for c in online + offline + avoid:
        h = host_for(c)
        if h not in hosts:
            hosts.append(h)
    known = {c.ip.lower() for c in candidates} | {h.lower() for h in hosts}
    if target_hostname and th not in known:
        hosts.append(target_hostname)
    return hosts


def resolve_pve_endpoints(db: Session, target: PveTarget, *, avoid_node_id=None) -> tuple:
    """Cluster-aware endpoint list (see order_endpoint_hosts), plus the API
    port. Always returns at least one host; with no cluster known yet (before
    the first discovery) it is just the configured target.hostname. The
    clients try these in order and fail over on connection errors."""
    cluster = db.query(Cluster).filter(Cluster.pve_target_id == target.id).one_or_none()
    if cluster is None:
        return [target.hostname], target.api_port

    nodes = (
        db.query(Node)
        .filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False), Node.management_ip.isnot(None))
        .all()
    )
    candidates = [EndpointCandidate(n.id, n.name, n.management_ip, n.status == "online") for n in nodes]
    hosts = order_endpoint_hosts(
        candidates,
        target_hostname=target.hostname,
        avoid_node_id=avoid_node_id,
        domain=_domain_of(target.hostname),
        dns_ok=_dns_resolves_to,
    )
    return (hosts or [target.hostname]), target.api_port


def resolve_pve_endpoint(db: Session, target: PveTarget, *, avoid_node_id=None) -> tuple:
    """First choice only -- kept for callers that want a single endpoint."""
    hosts, port = resolve_pve_endpoints(db, target, avoid_node_id=avoid_node_id)
    return hosts[0], port


def load_pve_credentials(db: Session, target: PveTarget, slot_name: str, *, avoid_node_id=None) -> PveCredentials:
    cred = (
        db.query(PveCredential)
        .filter(PveCredential.pve_target_id == target.id, PveCredential.slot_name == slot_name)
        .one_or_none()
    )
    if cred is None:
        raise CredentialNotConfigured(
            f"no {slot_name!r} credential slot configured for PVE target {target.name!r}"
        )
    secret = decrypt_secret(cred.encrypted_secret)
    hosts, api_port = resolve_pve_endpoints(db, target, avoid_node_id=avoid_node_id)
    return PveCredentials(
        hostname=hosts[0],
        fallback_hostnames=hosts[1:],
        api_port=api_port,
        token_user=cred.token_user,
        token_id=cred.token_id,
        token_secret=secret,
        tls_verify=target.tls_verify,
    )


def load_host_maintenance_credentials(db: Session, target: PveTarget, node: Node) -> HostMaintenanceCredentials:
    """Host-maintenance SSH creds are a completely separate table/trust
    model from PveCredential -- see HostMaintenanceCredential's docstring.
    Always connects to `node`'s own management_ip directly (no cluster-wide
    failover here -- W4 SSH always targets one specific node)."""
    cred = (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target.id)
        .one_or_none()
    )
    if cred is None:
        raise CredentialNotConfigured(
            f"no host-maintenance SSH credential configured for PVE target {target.name!r}"
        )
    if not node.management_ip:
        raise HostNotAddressable(
            f"no management_ip on record for node {node.name!r} yet -- run discovery first"
        )
    secret = decrypt_secret(cred.encrypted_private_key)
    return HostMaintenanceCredentials(
        hostname=node.management_ip,
        port=cred.ssh_port,
        username=cred.ssh_username,
        private_key_pem=secret,
        expected_host_key_type=node.ssh_host_key_type,
        expected_host_key_base64=node.ssh_host_key_base64,
    )
