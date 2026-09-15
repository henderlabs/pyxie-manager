"""Read-only maintenance planner. Generates a proposed plan for taking one
node down for maintenance -- classifies each resident workload's evacuation
eligibility and flags safety-rule blockers. There is no execute path here;
the only outputs are a persisted MaintenancePlan + MaintenancePlanWorkload
rows the API can read back.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .discovery import build_pve_client  # reuse the exact same credential-loading path as discovery
from .models import (
    Cluster,
    MaintenancePlan,
    MaintenancePlanWorkload,
    Node,
    ProtectionResult,
    PveTask,
    PveTarget,
    Storage,
    Workload,
)


def _quorum_after_removal(db: Session, cluster: Cluster, target: PveTarget, excluding_node_name: str, *, avoid_node_id=None) -> dict:
    """Live quorum check against PVE's own /cluster/status -- NOT a count
    of PyXie's own Node rows. The prior version counted every configured,
    not-missing Node and assumed all of them were online, so a cluster
    already running with one node down still reported quorum_holds=true
    for removing a SECOND node -- a real, dangerous gap found live.
    total_nodes/threshold come from the corosync member list PVE itself
    reports right now; the online set is live, not assumed -- a node
    already offline for any other reason is not silently treated as a
    safe vote to lose. Also requires the cluster to be quorate RIGHT NOW;
    nothing is safe to remove from an already non-quorate cluster."""
    try:
        client, _cred = build_pve_client(db, target, avoid_node_id=avoid_node_id)
        with client:
            status = client.cluster_status()
    except Exception as exc:
        return {"evaluated": False, "reason": f"could not read live cluster status: {exc}"}

    node_entries = [e for e in status if e.get("type") == "node"]
    cluster_entry = next((e for e in status if e.get("type") == "cluster"), None)
    total_nodes = len(node_entries)
    if total_nodes <= 1:
        return {"evaluated": False, "reason": "single-node cluster, quorum concept doesn't apply"}

    currently_quorate = bool(cluster_entry and cluster_entry.get("quorate"))
    online_now = {e.get("name") for e in node_entries if e.get("online")}
    remaining_online = online_now - {excluding_node_name}

    threshold = total_nodes // 2 + 1
    remaining = len(remaining_online)
    return {
        "evaluated": True,
        "currently_quorate": currently_quorate,
        "total_nodes": total_nodes,
        "online_now": sorted(n for n in online_now if n),
        "quorum_threshold": threshold,
        "remaining_after_removal": remaining,
        "quorum_holds": currently_quorate and remaining >= threshold,
        "margin_for_second_failure": remaining - threshold,
    }


def _destination_candidates(db: Session, cluster: Cluster, source_node: Node, required_bytes: int | None):
    candidates = []
    for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.id != source_node.id, Node.is_missing.is_(False), Node.status == "online", Node.maintenance_mode.is_(False)).all():
        allocated = sum(
            (w.memory_bytes or 0)
            for w in db.query(Workload).filter(
                Workload.node_id == n.id, Workload.is_missing.is_(False), Workload.status == "running",
            ).all()
        )
        headroom = (n.mem_total_bytes - allocated) if n.mem_total_bytes else None
        fits = headroom is not None and (required_bytes is None or headroom >= required_bytes)
        candidates.append({"node": n.name, "headroom_bytes": headroom, "fits": fits})
    return candidates


def _qemu_config(client, node_name: str, wl: Workload) -> dict | None:
    if wl.type != "vm" or client is None:
        return None
    try:
        return client.qemu_config(node_name, wl.vmid) or {}
    except Exception:
        return None


def _has_pci_passthrough(config: dict | None) -> bool | None:
    if config is None:
        return None
    return any(k.startswith("hostpci") for k in config.keys())


_DISK_KEY_PREFIXES = ("scsi", "virtio", "ide", "sata", "efidisk", "tpmstate")


def _workload_disk_storage_names(config: dict) -> set[str]:
    """Storage names this VM's actual disks (not cdrom/passthrough/net
    devices) are placed on, parsed from its live qemu config -- e.g.
    'local-lvm:vm-101-disk-0,size=32G' -> 'local-lvm'."""
    names = set()
    for key, value in config.items():
        if not any(key.rstrip("0123456789") == p for p in _DISK_KEY_PREFIXES):
            continue
        val = str(value)
        if "media=cdrom" in val:
            continue  # ISO-backed virtual CD-ROM, not an actual VM disk
        storage_name = val.split(":", 1)[0].strip()
        if storage_name:
            names.add(storage_name)
    return names


def workload_node_local_disk_storages(db: Session, node: Node, config: dict | None) -> list[str] | None:
    """Which of this workload's actual disks (per its live qemu config,
    NOT a per-node blanket check) sit on node-local storage. [] means every
    disk is on shared/external storage -- eligible for live migration from a
    storage-locality standpoint. None means unknown (config unavailable) --
    callers must fail toward the conservative 'offline_migration_required'
    path, never toward a false 'safe', same as everywhere else in this
    module.

    Replaces the earlier Phase 0/1 heuristic that flagged EVERY workload on
    a node as needing offline migration if that node had ANY node-local
    storage at all -- true for essentially every real PVE node (they always
    have a default 'local'/'local-lvm'), which made live-migration
    eligibility never actually trigger in practice. This checks the
    specific storage each disk is actually on.
    """
    if config is None:
        return None
    disk_storages = _workload_disk_storage_names(config)
    if not disk_storages:
        return []
    node_local_names = {
        s.name for s in db.query(Storage).filter(Storage.node_id == node.id, Storage.scope == "node-local").all()
    }
    return sorted(disk_storages & node_local_names)


# ---------------------------------------------------------------------------
# Migration gates: CRS/HA-affinity and CPU compatibility. Previously
# explicitly-not-evaluated (see README "Known limitations"); this is the
# write-capability-pass prerequisite that closes that gap before any
# automated evacuation is allowed. Both are best-effort against PVE's own
# APIs and fail toward "flag as unknown/blocked", never toward a false
# "safe" -- same rule the rest of the planner already follows.
# ---------------------------------------------------------------------------


def check_crs_affinity(client, wl: Workload, candidate_node_names: list[str]) -> list[str]:
    """Returns blocking reasons, if any, from HA groups (legacy, all
    versions) and HA affinity/anti-affinity rules (PVE 8.3+, best-effort --
    /cluster/ha/rules returns [] on older PVE, which this treats as 'not
    applicable', not as 'no rules exist' since we can't tell the difference
    from an empty list alone)."""
    reasons: list[str] = []
    sid = f"{'vm' if wl.type == 'vm' else 'ct'}:{wl.vmid}"

    resources = client.ha_resources()
    ha_entry = next((r for r in resources if r.get("sid") == sid), None)
    if ha_entry and ha_entry.get("group"):
        groups = client.ha_groups()
        group = next((g for g in groups if g.get("group") == ha_entry["group"]), None)
        if group:
            restricted = bool(group.get("restricted"))
            member_nodes = set()
            for entry in (group.get("nodes") or "").split(","):
                name = entry.split(":")[0].strip()
                if name:
                    member_nodes.add(name)
            if restricted and member_nodes:
                allowed_candidates = [n for n in candidate_node_names if n in member_nodes]
                if not allowed_candidates:
                    reasons.append(
                        f"HA group '{ha_entry['group']}' restricts this workload to "
                        f"{sorted(member_nodes)}, none of which are eligible destination candidates"
                    )

    rules = client.ha_rules()
    for rule in rules:
        rule_type = rule.get("type")
        rule_resources = rule.get("resources") or ""
        if sid not in [r.strip() for r in rule_resources.split(",")]:
            continue
        if rule_type == "node-affinity":
            # PVE's own --nodes syntax is <node>[:<pri>]{,<node>[:<pri>]}*
            # (confirmed against the official API: `pvesh usage
            # /cluster/ha/rules/{rule}`) -- a priority suffix is normal,
            # valid syntax, not an edge case. The legacy HA-groups path
            # above already strips it; this rules path didn't, so a
            # strict rule like "nodes=pve-node-2:2" incorrectly rejected
            # pve-node-2 itself as an eligible candidate.
            nodes = [n.split(":")[0].strip() for n in (rule.get("nodes") or "").split(",") if n.strip()]
            strict = bool(rule.get("strict", True))
            if strict and nodes and not any(n in candidate_node_names for n in nodes):
                reasons.append(
                    f"HA node-affinity rule '{rule.get('name', rule.get('rule'))}' requires "
                    f"one of {nodes}, none of which are eligible destination candidates"
                )
        elif rule_type in ("resource-affinity", "resource-anti-affinity"):
            # These describe co-location constraints between OTHER workloads,
            # not a node list -- flagging their existence is the honest
            # best-effort here; fully resolving co-location placement is the
            # standalone placement solver explicitly deferred in the collab
            # review, not this pass.
            reasons.append(
                f"workload participates in HA {rule_type} rule "
                f"'{rule.get('name', rule.get('rule'))}' -- verify co-location constraints manually before migrating"
            )

    return reasons


def check_cpu_compatibility(client, source_node: str, target_node: str, wl: Workload) -> list[str]:
    """Best-effort CPU compatibility check. PVE only truly validates this at
    migrate-time; this is a pre-check to surface an obvious risk rather than
    a guarantee. Only VM workloads have a meaningful 'cpu' config setting."""
    if wl.type != "vm":
        return []
    reasons: list[str] = []
    try:
        vm_config = client.qemu_config(source_node, wl.vmid) or {}
    except Exception:
        return ["could not read VM config to check CPU compatibility -- treat as unverified"]

    cpu_setting = str(vm_config.get("cpu", "")).split(",")[0] or "kvm64"
    if cpu_setting not in ("host",):
        # Non-'host' CPU types (kvm64, x86-64-v2-AES, qemu64, etc.) are
        # explicitly designed by PVE/QEMU to be portable across differing
        # physical CPUs -- no further check needed.
        return []

    try:
        source_info = (client.node_status(source_node) or {}).get("cpuinfo", {})
        target_info = (client.node_status(target_node) or {}).get("cpuinfo", {})
    except Exception:
        return [f"VM is configured with cpu=host and node CPU info could not be read to compare -- treat as unverified"]

    source_model = source_info.get("model")
    target_model = target_info.get("model")
    if source_model and target_model and source_model != target_model:
        reasons.append(
            f"VM is configured with cpu=host; source node CPU model ({source_model}) differs from "
            f"destination ({target_model}) -- live migration will likely fail or require a CPU model change first"
        )
    return reasons


def generate_plan(db: Session, node: Node, actor: str = "user") -> MaintenancePlan:
    now = datetime.now(timezone.utc)
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()

    quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
    workloads = db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False)).all()

    active_tasks_on_node = (
        db.query(PveTask)
        .filter(PveTask.node_id == node.id, PveTask.status == "running")
        .count()
    )

    plan_blockers: list[str] = []
    if quorum.get("evaluated") and not quorum["quorum_holds"]:
        plan_blockers.append("SAFE-QUORUM-001")

    workload_rows = []
    classification_counts = {"live_migratable": 0, "offline_migration_required": 0, "shutdown_in_place": 0, "blocked": 0, "unknown": 0}

    client = None
    try:
        client, _cred = build_pve_client(db, target)
    except Exception:
        client = None

    with (client if client else _NullCtx()):
        for wl in workloads:
            reasons = []
            blocking_rules = []
            classification = "unknown"

            config = _qemu_config(client, node.name, wl)
            node_local_disks = workload_node_local_disk_storages(db, node, config)
            # config is None (couldn't read live config) OR node_local_disks is None
            # both fail toward "treat as node-local" (conservative), never toward "safe"
            node_local_storage = node_local_disks is None or len(node_local_disks) > 0

            protection = (
                db.query(ProtectionResult)
                .filter(ProtectionResult.workload_id == wl.id)
                .order_by(ProtectionResult.last_updated.desc())
                .first()
            )
            if protection is None or protection.protected != "true":
                reasons.append("no confirmed protection coverage")
            if protection and protection.active_operation:
                blocking_rules.append("SAFE-BACKUP-001")
                reasons.append("an active protection operation is in progress")

            if wl.ha_state:
                reasons.append(f"HA state: {wl.ha_state} -- verify HA handles relocation")
                blocking_rules.append("SAFE-HA-001")

            passthrough = _has_pci_passthrough(config)
            if passthrough:
                blocking_rules.append("SAFE-MIGRATE-001")
                reasons.append("PCI/device passthrough configured -- requires a destination with the same device")
                classification = "blocked"
            elif wl.status != "running":
                classification = "shutdown_in_place"
                reasons.append("workload is not currently running")
            elif node_local_storage:
                classification = "offline_migration_required"
                blocking_rules.append("SAFE-STORAGE-001")
                if node_local_disks:
                    reasons.append(
                        f"disk(s) on node-local storage ({', '.join(node_local_disks)}); "
                        f"live migration is not possible without shared storage"
                    )
                else:
                    reasons.append(
                        "could not confirm disk storage locality from live PVE config; "
                        "treating conservatively as requiring offline migration"
                    )
            else:
                destinations = _destination_candidates(db, cluster, node, wl.memory_bytes)
                eligible = [d for d in destinations if d["fits"]]
                if client and eligible:
                    crs_reasons = check_crs_affinity(client, wl, [d["node"] for d in eligible])
                    if crs_reasons:
                        blocking_rules.append("SAFE-CRS-001")
                        reasons.extend(crs_reasons)
                        eligible = []  # not a false "safe" -- re-evaluated below
                if eligible:
                    cpu_reasons = check_cpu_compatibility(client, node.name, eligible[0]["node"], wl) if client else []
                    if cpu_reasons:
                        blocking_rules.append("SAFE-CPU-001")
                        reasons.extend(cpu_reasons)
                if eligible and not any(r.startswith("SAFE-CPU") or r.startswith("SAFE-CRS") for r in blocking_rules):
                    classification = "live_migratable"
                elif eligible:
                    classification = "blocked"
                else:
                    classification = "blocked"
                    if "SAFE-CRS-001" not in blocking_rules:
                        blocking_rules.append("SAFE-MIGRATE-001")
                        reasons.append("no destination node has sufficient memory headroom")

            if wl.downtime_tolerance == "low" and classification in ("shutdown_in_place", "offline_migration_required"):
                blocking_rules.append("SAFE-DOWNTIME-001")
                reasons.append(
                    "workload is tagged downtime_tolerance='low' (cannot tolerate downtime) but this maintenance "
                    f"action would require it -- flagged for explicit review, not a routine warning"
                )

            classification_counts[classification] += 1
            workload_rows.append(
                {
                    "workload_id": wl.id,
                    "vmid": wl.vmid,
                    "name": wl.name,
                    "classification": classification,
                    "reasons": reasons,
                    "blocking_safety_rules": blocking_rules,
                }
            )
            plan_blockers.extend(blocking_rules)

    if active_tasks_on_node:
        plan_blockers.append("SAFE-ROLLBACK-001")

    plan_blockers = sorted(set(plan_blockers))
    protected_count = sum(
        1
        for wl in workloads
        if db.query(ProtectionResult).filter(ProtectionResult.workload_id == wl.id, ProtectionResult.protected == "true").first()
    )

    summary = {
        "node": node.name,
        "cluster": cluster.name,
        "quorum": quorum,
        "workload_count": len(workloads),
        "classification_counts": classification_counts,
        "protection": {"compliant": protected_count, "unknown_or_missing": len(workloads) - protected_count},
        "pending_updates": node.pending_updates,
        "active_pve_tasks_on_node": active_tasks_on_node,
        "estimated_outage_count": classification_counts["shutdown_in_place"] + classification_counts["blocked"],
        "status_label": "Blocked" if classification_counts["blocked"] or (quorum.get("evaluated") and not quorum["quorum_holds"]) else (
            "Maintenance possible with warnings" if (classification_counts["offline_migration_required"] or classification_counts["shutdown_in_place"]) else "Maintenance possible"
        ),
    }

    plan = MaintenancePlan(
        node_id=node.id,
        generated_at=now,
        inventory_state_at=node.last_seen,
        protection_state_at=now,
        metric_state_at=now,
        status="current",
        summary=summary,
        blocking_safety_rules=plan_blockers,
        created_by=actor,
    )
    db.add(plan)
    db.flush()

    for row in workload_rows:
        db.add(
            MaintenancePlanWorkload(
                plan_id=plan.id,
                workload_id=row["workload_id"],
                classification=row["classification"],
                reasons=row["reasons"],
                blocking_safety_rules=row["blocking_safety_rules"],
            )
        )

    db.commit()
    db.refresh(plan)
    return plan


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
