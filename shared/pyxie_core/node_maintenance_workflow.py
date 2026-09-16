"""Explicit node maintenance mode -- independent of any specific job.
Real-world precedent (vSphere/Hyper-V/Nutanix all converge on the same
shape): entering maintenance mode evacuates the node (live-migrate what
can move, gracefully shut down in place what can't -- identical plan shape
to maintenance.run's own evacuate stage) and marks it out of service.
Exiting restarts anything that was shut down for entry and marks it
available again -- it deliberately does NOT force-migrate anything back;
a node coming out of maintenance is just a normal placement candidate
again, exactly like Hyper-V's "Resume" is a separate, optional step from
its "Fail Roles Back".

host_update_workflow.dry_run_host_update and reboot_workflow.dry_run_reboot
both hard-require node.maintenance_mode -- no host gets patched or
rebooted without first being explicitly taken out of service. A
maintenance.run satisfies this itself by setting/clearing the same flag
around its own already-existing evacuate/patch/reboot/restore sequence
(see maintenance_workflow.py) -- this module is what you use standalone,
for any reason at all, patching or not.
"""

from sqlalchemy.orm import Session

from . import lifecycle_workflow
from .credentials import CredentialNotConfigured, HostNotAddressable, load_host_maintenance_credentials
from .host_maintenance_client import HostMaintenanceClient, HostMaintenanceConnectionError, HostMaintenanceProtocolError
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .maintenance import _has_pci_passthrough, _qemu_config, _quorum_after_removal
from .migration_workflow import (
    MigrationWorkflowError,
    approve as approve_migration,
    dry_run_migration,
    execute_migration,
)
from .models import Cluster, Node, Operation, PveTarget, Storage, Workload, now_utc
from .operations_engine import (
    TERMINAL_STATUSES,
    OperationError,
    approve_operation,
    block_operation,
    check_cancel_requested,
    create_operation,
    enter_stage,
    fail_operation,
)
from .placement import current_storage_name, get_cluster_storage_preference, is_currently_on_shared_storage, rank_with_simulated_load, recommend_destinations, recommend_storage_for_candidate
from .discovery import build_pve_client


class NodeMaintenanceWorkflowError(Exception):
    pass


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return cluster, target


def _get_live_reboot_required(db: Session, target: PveTarget, node: Node) -> bool | None:
    """Best-effort, read-only reboot-required check via the host-maintenance
    wrapper. Returns None (unknown) if the kit isn't provisioned, reachable,
    or pinned on this node -- that's a separate, already-surfaced gap (the
    Host Maintenance panel), not something exit-maintenance should treat as
    equivalent to 'reboot required'. Only a definitive True gates anything.
    Lives here (not host_maintenance_client.py) because host_maintenance_client
    can't import from credentials.py -- credentials.py already imports FROM
    host_maintenance_client.py, and this file already safely depends on both."""
    try:
        creds = load_host_maintenance_credentials(db, target, node)
        with HostMaintenanceClient(creds, connect_timeout=5.0) as client:
            status_info = client.status()
        return status_info.get("reboot_required")
    except (CredentialNotConfigured, HostNotAddressable, HostMaintenanceConnectionError, HostMaintenanceProtocolError):
        return None


# ---------------------------------------------------------------------------
# Enter maintenance mode
# ---------------------------------------------------------------------------

def dry_run_enter_maintenance(db: Session, node: Node, *, actor: str, reason: str | None = None) -> Operation:
    cluster, target = _load_context(db, node)
    op = create_operation(
        db, "node.enter_maintenance", cluster_id=cluster.id, node_id=node.id,
        context={"node": node.name, "reason": reason}, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []

    if node.maintenance_mode:
        reasons.append(f"{node.name} is already in maintenance mode")
        blocking_rules.append("SAFE-MAINTMODE-001")

    quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
    if quorum.get("evaluated") and not quorum["quorum_holds"]:
        reasons.append("this node's removal would leave the cluster with insufficient quorum margin")
        blocking_rules.append("SAFE-QUORUM-001")

    workloads = db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False)).all()
    running_vms = [w for w in workloads if w.type == "vm" and w.status == "running"]
    stopped_workloads = [w for w in workloads if w.status != "running"]

    migrate_plan = []
    shutdown_plan = []
    blocked_workloads = []
    # A stopped workload costs this node nothing while it's off, so it's
    # never REQUIRED for entering maintenance -- but every workload on the
    # host still needs to be visible with a real action choice, not just
    # the running ones: a full evacuation still requires some type of
    # action/placement for every guest on the host. Even "leave it
    # stopped" is a placement decision, it just shouldn't be an invisible
    # default.
    stopped_no_action = []
    stopped_relocation_required = []

    client = None
    try:
        client, _cred = build_pve_client(db, target)
    except Exception as exc:
        reasons.append(f"could not build read client to plan evacuation: {exc}")

    if client:
        with client:
            candidates_all = (
                db.query(Node)
                .filter(Node.cluster_id == cluster.id, Node.id != node.id, Node.is_missing.is_(False), Node.status == "online", Node.maintenance_mode.is_(False))
                .all()
            )
            nodes_by_id = {n.id: n for n in candidates_all}
            simulated_added_bytes: dict = {}
            for wl in running_vms:
                config = _qemu_config(client, node.name, wl)
                if _has_pci_passthrough(config):
                    blocked_workloads.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                                               "reasons": ["PCI/device passthrough -- no safe automated path"]})
                    continue
                ranked = recommend_destinations(db, client, wl, candidates_all, simulated_added_bytes=simulated_added_bytes)
                ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
                top = next((c for c in ranked if not c.blocked), None)
                if top:
                    destination_node = db.query(Node).filter(Node.id == top.node_id).one()
                    simulated_added_bytes[destination_node.id] = simulated_added_bytes.get(destination_node.id, 0) + (wl.memory_bytes or 0)
                    currently_on_shared = is_currently_on_shared_storage(client, node, wl, db)
                    current_storage = current_storage_name(client, node, wl)
                    effective_pref = wl.storage_preference or get_cluster_storage_preference(db, cluster.id)
                    storage_rec = recommend_storage_for_candidate(db, destination_node.id, currently_on_shared, storage_preference=effective_pref)
                    migrate_plan.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "destination_node_id": str(destination_node.id), "destination_node": destination_node.name,
                        "destination_storage_id": storage_rec["id"] if storage_rec else None,
                        # Persisted so the edit-plan endpoint can recompute
                        # storage for an alternate destination purely from
                        # the DB, without needing a fresh PVE client call.
                        "currently_on_shared": currently_on_shared,
                        "current_storage": current_storage,
                        "storage_preference": effective_pref,
                        # Full ranked field, not just the pick -- lets the
                        # approval preview offer an alternate destination
                        # instead of only ever showing the auto-chosen one,
                        # so everything it's going to do has options
                        # selectable right there. Blocked candidates are
                        # included too, greyed out with their reason, same
                        # as Balance Load already does elsewhere.
                        "candidates": [
                            {
                                "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score,
                                "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                            }
                            for c in ranked
                        ],
                        # Per-VM live vs. shutdown/migrate/power-on choice,
                        # editable in the same plan preview as the
                        # destination override -- defaults to live so
                        # existing behavior doesn't change unless picked
                        # (live migration can be too costly time-wise with
                        # node-local storage).
                        "transport": "live",
                    })
                else:
                    if wl.downtime_tolerance == "low":
                        blocked_workloads.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                                                   "reasons": ["cannot live-migrate AND tagged downtime_tolerance='low' -- no safe automated path"]})
                    else:
                        # No UNBLOCKED candidate -- commonly headroom, which
                        # is a resource judgment call, not a hard fact (a
                        # batch plan's headroom is a snapshot that changes
                        # as earlier moves in the SAME plan land for real,
                        # and the "blocked" score itself is conservative).
                        # Defaults to shutting down in place (included:
                        # False, today's behavior) but every candidate --
                        # blocked or not -- is still offered so a human can
                        # opt this into a real move instead, accepting
                        # whatever's flagged. Insufficient memory headroom
                        # isn't a constant -- every workload should be
                        # displayed for choice of handling regardless of
                        # headroom.
                        fallback = ranked[0] if ranked else None
                        item = {
                            "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                            "reasons": fallback.blocking_reasons if fallback else ["no other online node in this cluster"],
                            "included": False,
                            # Distinguishes this from a stopped_relocation_required
                            # row in the same combined UI list -- this one IS
                            # currently running, so live vs. offline transport
                            # is a real, relevant choice, and the UI should
                            # note when a workload is stopped vs. running.
                            "currently_running": True,
                            "candidates": [
                                {
                                    "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score,
                                    "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                                }
                                for c in ranked
                            ],
                            "transport": "live",
                        }
                        if fallback is not None:
                            destination_node = db.query(Node).filter(Node.id == fallback.node_id).one()
                            currently_on_shared = is_currently_on_shared_storage(client, node, wl, db)
                            effective_pref = wl.storage_preference or get_cluster_storage_preference(db, cluster.id)
                            storage_rec = recommend_storage_for_candidate(db, destination_node.id, currently_on_shared, storage_preference=effective_pref)
                            item.update({
                                "destination_node_id": str(destination_node.id), "destination_node": destination_node.name,
                                "destination_storage_id": storage_rec["id"] if storage_rec else None,
                                "currently_on_shared": currently_on_shared, "storage_preference": effective_pref,
                            })
                        shutdown_plan.append(item)

            for wl in stopped_workloads:
                try:
                    on_shared = is_currently_on_shared_storage(client, node, wl, db)
                except Exception as exc:
                    stopped_relocation_required.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": [f"could not determine storage locality ({exc}) -- manual review recommended"],
                        "included": False, "currently_running": False,
                    })
                    continue
                if on_shared:
                    stopped_no_action.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": ["stopped, and its disk is on shared storage -- no action needed for maintenance mode"],
                    })
                else:
                    # Never required -- entering maintenance mode is
                    # temporary (the node stays in the cluster and comes
                    # back), so a stopped guest just sits there either way
                    # -- but still shown with a real destination choice so
                    # it's never silently invisible.
                    ranked = recommend_destinations(db, client, wl, candidates_all, simulated_added_bytes=simulated_added_bytes)
                    ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
                    top = next((c for c in ranked if not c.blocked), None) or (ranked[0] if ranked else None)
                    item = {
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": ["stopped, with its disk on node-local storage -- fine to leave as-is for this "
                                    "temporary maintenance cycle (it stays put and comes back with the node), "
                                    "relocate only if you want to for some other reason"],
                        "included": False,
                        "currently_running": False,
                    }
                    if top is not None:
                        destination_node = db.query(Node).filter(Node.id == top.node_id).one()
                        storage_rec = recommend_storage_for_candidate(db, destination_node.id, False)
                        item.update({
                            "destination_node_id": str(destination_node.id), "destination_node": destination_node.name,
                            "destination_storage_id": storage_rec["id"] if storage_rec else None,
                            "currently_on_shared": False,
                            "candidates": [
                                {
                                    "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score,
                                    "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                                }
                                for c in ranked
                            ],
                        })
                    stopped_relocation_required.append(item)
    else:
        blocked_workloads = [{"workload_id": str(w.id), "vmid": w.vmid, "name": w.name, "reasons": ["no read client available"]} for w in running_vms]
        stopped_relocation_required = [{"workload_id": str(w.id), "vmid": w.vmid, "name": w.name,
                                         "reasons": ["no read client available -- could not classify"], "included": False,
                                         "currently_running": False}
                                        for w in stopped_workloads]

    if blocked_workloads:
        reasons.append(
            f"{len(blocked_workloads)} workload(s) have no safe automated evacuation path: "
            + ", ".join(f"{b['name'] or b['vmid']}" for b in blocked_workloads)
        )
        blocking_rules.append("SAFE-MIGRATE-001")

    dry_run_result = {
        "node": node.name,
        "reason": reason,
        "eligible": len(blocking_rules) == 0,
        "reasons": reasons,
        "blocking_safety_rules": blocking_rules,
        "migrate_plan": migrate_plan,
        "shutdown_plan": shutdown_plan,
        "blocked_workloads": blocked_workloads,
        "stopped_no_action": stopped_no_action,
        "stopped_relocation_required": stopped_relocation_required,
    }
    context = {
        "node": node.name, "reason": reason, "migrate_plan": migrate_plan, "shutdown_plan": shutdown_plan,
        "stopped_relocation_required": stopped_relocation_required,
    }

    if blocking_rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, context=context, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, context=context, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_enter_maintenance(db: Session, operation_id) -> Operation:
    """Resumable -- progress tracked in op.context exactly like
    maintenance.run's own evacuate stage."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise NodeMaintenanceWorkflowError(f"operation {operation_id} not found")

    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _load_context(db, node)
    ctx = op.context or {}
    migrate_plan = ctx.get("migrate_plan", [])
    shutdown_plan = ctx.get("shutdown_plan", [])
    included_stopped = [item for item in ctx.get("stopped_relocation_required", []) if item.get("included")]
    completed_migrations = list(ctx.get("completed_migrations", []))
    completed_shutdowns = list(ctx.get("completed_shutdowns", []))
    completed_stopped = list(ctx.get("completed_stopped_relocation_ids", []))

    def save_progress():
        op.context = {
            **ctx, "migrate_plan": migrate_plan, "shutdown_plan": shutdown_plan,
            "completed_migrations": completed_migrations, "completed_shutdowns": completed_shutdowns,
            "completed_stopped_relocation_ids": completed_stopped,
        }
        db.commit()

    try:
        if op.status == "approved":
            # Re-check quorum with LIVE state right before evacuating -- the
            # dry-run's check can be stale by the time a human approves the
            # plan (another node could have gone offline in the meantime).
            quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
            if quorum.get("evaluated") and not quorum["quorum_holds"]:
                return block_operation(db, op, blocking_safety_rules=["SAFE-QUORUM-001"], actor="system")
            try:
                # 12600s (3.5h) matches this operation type's own RQ job
                # timeout (10800s/3h) plus a 30min margin, same reasoning
                # as node.evacuate.
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="node.enter_maintenance", ttl_seconds=12600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            op = enter_stage(db, op, status="evacuating", stage="evacuating")

        if op.status == "evacuating":
            for item in migrate_plan:
                if item["workload_id"] in completed_migrations or item["workload_id"] in completed_shutdowns:
                    continue
                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled
                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if workload is None or workload.is_missing:
                    completed_migrations.append(item["workload_id"])
                    save_progress()
                    continue

                # A workload with a perfectly good auto-found destination
                # can still be told "just shut it down instead" -- not
                # every guest needs the migration machinery, and for a lot
                # of them a brief power-cycle during the maintenance window
                # is simpler and just as acceptable as a live/offline move:
                # some workloads can stay in place and just be powered
                # down during updates, then brought back up once
                # updates/reboot are complete. Tracked in completed_shutdowns (not
                # completed_migrations) so node.exit_maintenance's own
                # restart_plan -- which already reads exactly that list --
                # brings it back up here with zero new plumbing needed.
                if item.get("transport") == "shutdown_in_place":
                    child = lifecycle_workflow.dry_run_shutdown(
                        db, workload, actor=f"node.enter_maintenance:{op.id}",
                        parent_operation_id=op.id, correlation_id=op.correlation_id,
                    )
                    if child.status == "blocked":
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                    child = lifecycle_workflow.approve(db, child, approved_by=f"node.enter_maintenance:{op.id}")
                    child = lifecycle_workflow.execute_lifecycle_action(db, child.id)
                    if child.status != "completed":
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"graceful shutdown for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                    completed_shutdowns.append(item["workload_id"])
                    save_progress()
                    continue

                destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                if destination_node is None:
                    completed_migrations.append(item["workload_id"])
                    save_progress()
                    continue
                destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none() if item.get("destination_storage_id") else None
                try:
                    child = dry_run_migration(
                        db, workload, destination_node, actor=f"node.enter_maintenance:{op.id}",
                        destination_storage=destination_storage, transport=item.get("transport", "live"),
                        # A manually-picked destination has already had its
                        # blocking reasons (including headroom) shown to a
                        # human in the plan editor -- an auto-pick never
                        # needs this since recommend_destinations() only
                        # auto-selects unblocked candidates in the first
                        # place, and every workload should be displayed
                        # for choice of handling regardless of headroom.
                        confirm_override_headroom=bool(item.get("manually_set")),
                        parent_operation_id=op.id, correlation_id=op.correlation_id,
                    )
                except MigrationWorkflowError as exc:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"could not plan migration for vmid {item['vmid']}: {exc}")
                if child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                child = approve_migration(db, child, approved_by=f"node.enter_maintenance:{op.id}")
                child = execute_migration(db, child.id)
                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"migration for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                completed_migrations.append(item["workload_id"])
                save_progress()

            for item in shutdown_plan:
                if item["workload_id"] in completed_shutdowns:
                    continue
                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled
                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if workload is None or workload.is_missing or workload.status != "running":
                    completed_shutdowns.append(item["workload_id"])
                    save_progress()
                    continue

                # Opted into a real move instead of shutting down in place --
                # the destination shown here may itself be flagged (e.g.
                # insufficient headroom); a human explicitly including this
                # row is exactly the override that check now honors, since
                # every workload should be displayed for choice of
                # handling regardless of headroom.
                if item.get("included") and item.get("destination_node_id"):
                    destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                    if destination_node is None:
                        completed_shutdowns.append(item["workload_id"])
                        save_progress()
                        continue
                    destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none() if item.get("destination_storage_id") else None
                    try:
                        child = dry_run_migration(
                            db, workload, destination_node, actor=f"node.enter_maintenance:{op.id}",
                            destination_storage=destination_storage, transport=item.get("transport", "live"),
                            confirm_override_headroom=True,
                            parent_operation_id=op.id, correlation_id=op.correlation_id,
                        )
                    except MigrationWorkflowError as exc:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"could not plan migration for vmid {item['vmid']}: {exc}")
                    if child.status == "blocked":
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                    child = approve_migration(db, child, approved_by=f"node.enter_maintenance:{op.id}")
                    child = execute_migration(db, child.id)
                    if child.status != "completed":
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"migration for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                    completed_shutdowns.append(item["workload_id"])
                    save_progress()
                    continue

                child = lifecycle_workflow.dry_run_shutdown(
                    db, workload, actor=f"node.enter_maintenance:{op.id}",
                    parent_operation_id=op.id, correlation_id=op.correlation_id,
                )
                if child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                child = lifecycle_workflow.approve(db, child, approved_by=f"node.enter_maintenance:{op.id}")
                child = lifecycle_workflow.execute_lifecycle_action(db, child.id)
                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"graceful shutdown for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                completed_shutdowns.append(item["workload_id"])
                save_progress()

            # Opt-in stopped-VM relocations -- never required for a
            # temporary enter-maintenance cycle (a stopped guest costs this
            # node nothing while it's off), only actually moved if a human
            # explicitly included one before approving: a full evacuation
            # still requires some type of action/placement for every
            # workload, even a stopped one whose choice is usually "leave
            # it". Same reconcile-before-
            # create pattern as the loops above, kept separate to avoid
            # touching their already-proven logic.
            for item in included_stopped:
                if item["workload_id"] in completed_stopped:
                    continue
                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled
                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if workload is None or workload.is_missing:
                    completed_stopped.append(item["workload_id"])
                    save_progress()
                    continue
                destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                if destination_node is None:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"destination node for stopped vmid {item['vmid']} disappeared from inventory")
                destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none() if item.get("destination_storage_id") else None

                existing_child = (
                    db.query(Operation)
                    .filter(
                        Operation.parent_operation_id == op.id,
                        Operation.operation_type_id == "vm.live_migrate",
                        Operation.workload_id == workload.id,
                    )
                    .order_by(Operation.created_at.desc())
                    .first()
                )
                if existing_child is not None and existing_child.status == "completed":
                    child = existing_child
                elif existing_child is not None and existing_child.status in ("failed", "blocked"):
                    release_locks_for_operation(db, op.id)
                    if existing_child.status == "blocked":
                        return block_operation(db, op, blocking_safety_rules=(existing_child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                    return fail_operation(db, op, error=f"relocation for stopped vmid {item['vmid']} (operation {existing_child.id}) previously failed: {existing_child.error}")
                elif existing_child is not None:
                    child = existing_child
                    if child.status == "awaiting_approval":
                        child = approve_migration(db, child, approved_by=f"node.enter_maintenance:{op.id}")
                    if child.status not in TERMINAL_STATUSES:
                        child = execute_migration(db, child.id)
                else:
                    try:
                        child = dry_run_migration(
                            db, workload, destination_node, actor=f"node.enter_maintenance:{op.id}",
                            destination_storage=destination_storage, transport="offline",
                            confirm_override_headroom=True,
                            parent_operation_id=op.id, correlation_id=op.correlation_id,
                        )
                    except MigrationWorkflowError as exc:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"could not plan relocation for stopped vmid {item['vmid']}: {exc}")
                    if child.status == "blocked":
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                    child = approve_migration(db, child, approved_by=f"node.enter_maintenance:{op.id}")
                    child = execute_migration(db, child.id)

                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"relocation for stopped vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                completed_stopped.append(item["workload_id"])
                save_progress()

            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            still_running = db.query(Workload).filter(
                Workload.node_id == node.id, Workload.is_missing.is_(False), Workload.status == "running"
            ).count()
            if still_running:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{still_running} workload(s) still running on {node.name} after evacuation")

            node.maintenance_mode = True
            node.maintenance_mode_since = now_utc()
            node.maintenance_reason = ctx.get("reason")
            node.maintenance_mode_by = op.created_by
            db.commit()
            release_locks_for_operation(db, op.id)
            return enter_stage(db, op, status="completed", stage="audit")

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status}, stage={op.stage})")

    except Exception as exc:  # noqa: BLE001 -- always record, never silently drop
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))


# ---------------------------------------------------------------------------
# Exit maintenance mode
# ---------------------------------------------------------------------------

def dry_run_exit_maintenance(db: Session, node: Node, *, actor: str) -> Operation:
    cluster, target = _load_context(db, node)
    op = create_operation(
        db, "node.exit_maintenance", cluster_id=cluster.id, node_id=node.id,
        context={"node": node.name}, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []
    restart_plan = []

    if not node.maintenance_mode:
        reasons.append(f"{node.name} is not currently in maintenance mode")
        blocking_rules.append("SAFE-MAINTMODE-001")
    else:
        reboot_required = _get_live_reboot_required(db, target, node)
        if reboot_required:
            reasons.append(
                f"{node.name} still requires a reboot -- exiting maintenance mode now would put it back in "
                "service on an already-patched-but-not-rebooted kernel. Reboot it first (Actions -> Reboot Node)."
            )
            blocking_rules.append("SAFE-REBOOT-001")

        entry = (
            db.query(Operation)
            .filter(Operation.operation_type_id == "node.enter_maintenance", Operation.node_id == node.id, Operation.status == "completed")
            .order_by(Operation.created_at.desc())
            .first()
        )
        shutdown_ids = (entry.context or {}).get("completed_shutdowns", []) if entry else []
        for wid in shutdown_ids:
            workload = db.query(Workload).filter(Workload.id == wid).one_or_none()
            if workload is not None and not workload.is_missing and workload.status == "stopped":
                restart_plan.append({"workload_id": str(workload.id), "vmid": workload.vmid, "name": workload.name})

    dry_run_result = {
        "node": node.name,
        "eligible": len(blocking_rules) == 0,
        "reasons": reasons,
        "blocking_safety_rules": blocking_rules,
        "restart_plan": restart_plan,
        "note": "exiting does not migrate anything back onto this node -- workloads live-migrated off during "
                "entry stay wherever they were sent; only guests this run itself shut down get restarted.",
    }
    context = {"node": node.name, "restart_plan": restart_plan}

    if blocking_rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, context=context, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, context=context, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def execute_exit_maintenance(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise NodeMaintenanceWorkflowError(f"operation {operation_id} not found")

    node = db.query(Node).filter(Node.id == op.node_id).one()
    ctx = op.context or {}
    restart_plan = ctx.get("restart_plan", [])
    restarted = list(ctx.get("restarted", []))

    def save_progress():
        op.context = {**ctx, "restart_plan": restart_plan, "restarted": restarted}
        db.commit()

    try:
        if op.status == "approved":
            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="node.exit_maintenance", ttl_seconds=1800)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing":
            for item in restart_plan:
                if item["workload_id"] in restarted:
                    continue
                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if workload is None or workload.is_missing or workload.status != "stopped":
                    restarted.append(item["workload_id"])
                    save_progress()
                    continue
                child = lifecycle_workflow.dry_run_start(
                    db, workload, actor=f"node.exit_maintenance:{op.id}",
                    parent_operation_id=op.id, correlation_id=op.correlation_id,
                )
                if child.status == "awaiting_approval":
                    child = lifecycle_workflow.approve(db, child, approved_by=f"node.exit_maintenance:{op.id}")
                    child = lifecycle_workflow.execute_lifecycle_action(db, child.id)
                    # Non-fatal: restoring service on this one guest is a
                    # nicety, not a safety requirement -- note it and move on.
                restarted.append(item["workload_id"])
                save_progress()

            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            node.maintenance_mode = False
            node.maintenance_mode_since = None
            node.maintenance_reason = None
            node.maintenance_mode_by = None
            db.commit()
            release_locks_for_operation(db, op.id)
            return enter_stage(db, op, status="completed", stage="audit")

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status}, stage={op.stage})")

    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
