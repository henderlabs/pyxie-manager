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


def resolve_pve_endpoint(db: Session, target: PveTarget, *, avoid_node_id=None) -> tuple[str, int]:
    """Cluster-aware endpoint resolution. Prefers a healthy, online cluster
    member other than avoid_node_id (typically the node currently under
    maintenance/reboot) over the single configured target.hostname, so PyXie
    keeps talking to the cluster even while that one node is offline.

    Falls back to target.hostname/target.api_port (the old, single-endpoint
    behavior) whenever nothing better is known yet -- e.g. before the very
    first discovery run has populated any Node.management_ip, or if no
    cluster is on record for this target at all. Never refuses to return an
    endpoint outright; a stale-but-present fallback beats hard failure.
    """
    cluster = db.query(Cluster).filter(Cluster.pve_target_id == target.id).one_or_none()
    if cluster is None:
        return target.hostname, target.api_port

    candidates = (
        db.query(Node)
        .filter(
            Node.cluster_id == cluster.id,
            Node.is_missing.is_(False),
            Node.status == "online",
            Node.management_ip.isnot(None),
        )
        .order_by(Node.name)
        .all()
    )

    if avoid_node_id is not None:
        narrowed = [n for n in candidates if str(n.id) != str(avoid_node_id)]
        # If avoiding empties the candidate list (e.g. only one online node
        # is known), fall through to the unnarrowed list rather than refuse
        # to connect at all -- some connectivity beats none.
        if narrowed:
            candidates = narrowed

    if not candidates:
        return target.hostname, target.api_port

    # Prefer whichever candidate is the currently-configured endpoint, to
    # keep behavior stable and unsurprising when nothing is actually wrong;
    # otherwise take the first candidate (deterministic, sorted by name).
    preferred = next((n for n in candidates if n.management_ip == target.hostname), None)
    chosen = preferred or candidates[0]
    return chosen.management_ip, target.api_port


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
    hostname, api_port = resolve_pve_endpoint(db, target, avoid_node_id=avoid_node_id)
    return PveCredentials(
        hostname=hostname,
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
