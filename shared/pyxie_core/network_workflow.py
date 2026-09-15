"""Network tab: read-only host network topology (bridges/bonds/VLANs, from
`/nodes/{node}/network`) plus workload.network_vlan_change, the one write
path this module offers -- reassigning which VLAN a workload's NIC is on.

Deliberately does NOT offer any write path for host-level bridge/VLAN-aware
config: that's a pending-changes model requiring a network reload
(`ifreload`), and a bad apply can sever the node's own connectivity,
requiring console access to fix -- a different risk class from every other
PVE write in this codebase. Deliberately scoped to per-workload NIC VLAN
tag only.

A NIC's VLAN tag IS hot-pluggable on a running guest (unlike cores/memory),
so workload.network_vlan_change has no shutdown/start stages -- same
synchronous, no-PVE-task shape as protection.backup_membership. Stage
order: dry_run -> awaiting_approval -> revalidating -> executing ->
verifying -> audit.
"""

from sqlalchemy.orm import Session

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, PveTarget, Workload
from .operations_engine import (
    OperationError,
    approve_operation,
    block_operation,
    create_operation,
    enter_stage,
    fail_operation,
)
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback


class NetworkWorkflowError(Exception):
    pass


def _load_cluster_context(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return target


def _load_workload_context(db: Session, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = _load_cluster_context(db, cluster)
    return node, cluster, target


def _parse_net_display(net: str) -> dict:
    """Parse a PVE net-device string for display/validation. QEMU's
    leading token has no explicit key ('<model>=<macaddr>', e.g.
    'virtio=BC:24:...') -- captured here under 'model'/'mac' the first time
    an unrecognized key is seen; LXC's leading token ('name=eth0') is a
    real key and just passes through normally, so both guest types are
    handled by the same loop with no branching on guest type."""
    fields: dict = {}
    model_seen = False
    for part in net.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key in ("bridge", "tag", "firewall", "rate", "mtu", "link_down", "trunks", "name", "ip", "gw", "hwaddr", "macaddr"):
            fields[key] = value
        elif not model_seen:
            fields["model"], fields["mac"] = key, value
            model_seen = True
    return fields


def _vlan_allowed(tag: int, bridge_vids: str | None) -> bool:
    """bridge_vids is PVE's `bridge_vids` field on a VLAN-aware bridge, a
    space-separated list of single IDs and/or ranges (e.g. '2 4 100-200').
    Empty/unset means no explicit allow-list is configured -- PVE's own
    default in that case is to allow any valid VLAN ID, not none."""
    if not bridge_vids:
        return 1 <= tag <= 4094
    for token in bridge_vids.split():
        if "-" in token:
            lo, _, hi = token.partition("-")
            if lo.isdigit() and hi.isdigit() and int(lo) <= tag <= int(hi):
                return True
        elif token.isdigit() and int(token) == tag:
            return True
    return False


def list_network_topology(db: Session, cluster: Cluster) -> list[dict]:
    """Read-only: every node's network interfaces in this cluster, as PVE
    itself reports them. No write path touches this -- see module
    docstring."""
    target = _load_cluster_context(db, cluster)
    nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all()
    read_client, _cred = build_pve_client(db, target)

    topology = []
    with read_client:
        for node in nodes:
            try:
                raw_ifaces = read_client.node_network(node.name)
            except Exception as exc:  # noqa: BLE001 -- one unreachable node shouldn't blank the whole page
                topology.append({"node": node.name, "node_id": str(node.id), "error": str(exc), "interfaces": []})
                continue
            interfaces = [
                {
                    "iface": i.get("iface"),
                    "type": i.get("type"),
                    "vlan_aware": bool(i.get("bridge_vlan_aware")),
                    "allowed_vlans": i.get("bridge_vids") or None,
                    "ports": i.get("bridge_ports") or i.get("slaves"),
                    "active": bool(i.get("active", 1)),
                    "comments": i.get("comments"),
                }
                for i in raw_ifaces
                if i.get("iface") != "lo"
            ]
            topology.append({"node": node.name, "node_id": str(node.id), "interfaces": interfaces})
    return topology


def list_workload_nics(db: Session, cluster: Cluster) -> list[dict]:
    """Read-only: every workload's NIC(s) in this cluster, cross-referenced
    against list_network_topology() so the UI can show whether each NIC's
    bridge is VLAN-aware and which tags are actually allowed there. One
    live PVE config read per workload -- fine at homelab/pilot scale;
    a much larger cluster would need batching (known limitation, not
    solved here)."""
    workloads = (
        db.query(Workload)
        .filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False))
        .order_by(Workload.name)
        .all()
    )
    target = _load_cluster_context(db, cluster)
    read_client, _cred = build_pve_client(db, target)
    topo_by_node = {t["node"]: {i["iface"]: i for i in t["interfaces"]} for t in list_network_topology(db, cluster)}

    out = []
    with read_client:
        for wl in workloads:
            if wl.node is None:
                continue
            try:
                config = (
                    read_client.qemu_config(wl.node.name, wl.vmid)
                    if wl.type == "vm"
                    else read_client.lxc_config(wl.node.name, wl.vmid)
                )
            except Exception as exc:  # noqa: BLE001 -- one unreachable guest shouldn't blank the whole list
                out.append(
                    {
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name, "type": wl.type,
                        "node": wl.node.name, "status": wl.status, "error": str(exc), "nics": [],
                    }
                )
                continue

            nics = []
            for key, value in config.items():
                if not (key.startswith("net") and key[3:].isdigit()):
                    continue
                parsed = _parse_net_display(str(value))
                bridge = parsed.get("bridge")
                tag = int(parsed["tag"]) if str(parsed.get("tag", "")).isdigit() else None
                iface_info = topo_by_node.get(wl.node.name, {}).get(bridge or "", {})
                nics.append(
                    {
                        "net_id": key,
                        "bridge": bridge,
                        "vlan_tag": tag,
                        "model": parsed.get("model"),
                        "bridge_vlan_aware": bool(iface_info.get("vlan_aware")),
                        "allowed_vlans": iface_info.get("allowed_vlans"),
                    }
                )
            out.append(
                {
                    "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name, "type": wl.type,
                    "node": wl.node.name, "status": wl.status, "nics": nics,
                }
            )
    return out


def dry_run_vlan_change(db: Session, workload: Workload, *, net_id: str, new_tag: int | None, actor: str) -> Operation:
    node, cluster, target = _load_workload_context(db, workload)
    read_client, _cred = build_pve_client(db, target)
    with read_client:
        config = (
            read_client.qemu_config(node.name, workload.vmid)
            if workload.type == "vm"
            else read_client.lxc_config(node.name, workload.vmid)
        )
        topo = list_network_topology(db, cluster)

    current_net = config.get(net_id)
    if current_net is None:
        raise NetworkWorkflowError(f"{net_id} not found on this workload's current config")

    parsed = _parse_net_display(str(current_net))
    bridge = parsed.get("bridge")
    current_tag = int(parsed["tag"]) if str(parsed.get("tag", "")).isdigit() else None
    iface_info = next((i for t in topo if t["node"] == node.name for i in t["interfaces"] if i["iface"] == bridge), None)

    context = {
        "node_id": str(node.id), "node": node.name, "vmid": workload.vmid, "guest_type": workload.type,
        "net_id": net_id, "bridge": bridge, "current_net": str(current_net),
        "current_tag": current_tag, "new_tag": new_tag,
    }
    op = create_operation(
        db, "workload.network_vlan_change", cluster_id=cluster.id, node_id=node.id, workload_id=workload.id,
        context=context, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []
    if current_tag == new_tag:
        reasons.append(f"NIC {net_id} is already on VLAN {new_tag if new_tag is not None else '(untagged)'} -- nothing to change")
        blocking_rules.append("SAFE-NETWORK-000")
    elif bridge is None:
        reasons.append(f"{net_id} has no bridge configured -- can't validate VLAN membership")
        blocking_rules.append("SAFE-NETWORK-001")
    elif iface_info is None:
        reasons.append(f"bridge {bridge!r} not found in {node.name}'s current network config")
        blocking_rules.append("SAFE-NETWORK-001")
    elif new_tag is not None:
        if not iface_info.get("vlan_aware"):
            reasons.append(f"bridge {bridge!r} on {node.name} is not VLAN-aware -- it can't carry tagged traffic")
            blocking_rules.append("SAFE-NETWORK-002")
        elif not _vlan_allowed(new_tag, iface_info.get("allowed_vlans")):
            reasons.append(
                f"VLAN {new_tag} is not in {bridge!r}'s allowed list "
                f"({iface_info.get('allowed_vlans') or 'none configured on this bridge'})"
            )
            blocking_rules.append("SAFE-NETWORK-003")

    dry_run_result = {
        "vmid": workload.vmid, "workload_name": workload.name, "node": node.name,
        "net_id": net_id, "bridge": bridge, "current_tag": current_tag, "new_tag": new_tag,
        "eligible": len(blocking_rules) == 0, "reasons": reasons, "blocking_safety_rules": blocking_rules,
    }

    if blocking_rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_vlan_change(db: Session, operation_id) -> Operation:
    """Resumable, same convention as every other workflow module here."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise NetworkWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_workload_context(db, workload)
    ctx = op.context or {}
    net_id = ctx["net_id"]
    new_tag = ctx["new_tag"]
    guest_type = ctx["guest_type"]

    try:
        if op.status in ("approved", "revalidating"):
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                config = (
                    read_client.qemu_config(node.name, workload.vmid)
                    if guest_type == "vm"
                    else read_client.lxc_config(node.name, workload.vmid)
                )
            live_net = config.get(net_id)
            if live_net is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{net_id} no longer exists on this workload")
            # Someone editing this same NIC directly (PVE UI, another
            # session) between dry-run and approval is drift a human
            # should see, not paper over -- same pattern as every other
            # workflow's revalidation stage here.
            if str(live_net) != ctx["current_net"]:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-NETWORK-004"], actor="system")

            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason="workload.network_vlan_change")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing":
            maintenance_creds = load_pve_credentials(db, target, "maintenance")
            with PveMaintenanceClient(maintenance_creds) as write_client:
                write_client.set_vm_network_vlan(
                    node.name, workload.vmid, guest_type=guest_type, net_id=net_id,
                    current_net=ctx["current_net"], tag=new_tag,
                )
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                config = (
                    read_client.qemu_config(node.name, workload.vmid)
                    if guest_type == "vm"
                    else read_client.lxc_config(node.name, workload.vmid)
                )
            live_net = str(config.get(net_id) or "")
            parsed = _parse_net_display(live_net)
            live_tag = int(parsed["tag"]) if str(parsed.get("tag", "")).isdigit() else None
            op.verification_result = {"net": live_net, "tag": live_tag}
            db.commit()

            if live_tag != new_tag:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"read-back after VLAN change didn't match: tag={live_tag}, expected {new_tag}")

            release_locks_for_operation(db, op.id)
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
