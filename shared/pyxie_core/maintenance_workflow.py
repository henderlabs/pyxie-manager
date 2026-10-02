"""Stage W6: the end-to-end maintenance workflow, combining the proven W1-W3
and W5 components under one explicitly-approved run:

  preflight -> awaiting_approval -> evacuate (live-migrate what can move,
  gracefully shut down in place what can't) -> verify_evacuation ->
  patch (real Stage W4 execution via host_update_workflow.py, as a child
  operation -- same reuse pattern as the reboot below) -> reboot (SKIPPED
  when W4's own independent verification says no reboot is required;
  otherwise delegates to Stage W5 exactly as before) -> verify_node ->
  restore_placement (migrate back / start back up what this run moved or
  stopped) -> verify_workloads -> audit.

No scheduled/policy-driven automation anywhere in this module -- every run
is created by an explicit human action and every stage transition is
either automatic-but-already-approved-as-part-of-the-plan (matching W2's
child-migration pattern) or requires the operation to have been approved
at all. A workload that is neither live-migratable nor safe to shut down
(e.g. PCI passthrough) blocks the ENTIRE run at preflight -- there is no
partial/best-effort maintenance path for those.

"Restore placement" puts workloads back on the ORIGINAL node they were
evacuated from and restarts whatever was shut down -- it deliberately does
NOT re-run the placement/balance engine to find a "better" spot, since the
point of this stage is undoing what THIS maintenance run did, not
re-optimizing the cluster.
"""

from sqlalchemy.orm import Session

from . import host_update_workflow, lifecycle_workflow, reboot_workflow
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .maintenance import _quorum_after_removal, _has_pci_passthrough, _qemu_config
from .migration_workflow import (
    MigrationWorkflowError,
    approve as approve_migration,
    dry_run_migration,
    execute_migration,
)
from .models import Cluster, Node, Operation, PveTarget, Workload, now_utc
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
from .placement import note_planned_move, current_storage_name, get_cluster_storage_preference, is_currently_on_shared_storage, rank_with_simulated_load, recommend_destinations, recommend_storage_for_candidate
from .discovery import build_pve_client
from .node_maintenance_workflow import running_on_node_live


class MaintenanceWorkflowError(Exception):
    pass


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return cluster, target


def dry_run_maintenance(
    db: Session, node: Node, *, actor: str, force_reboot: bool = False, restore_after: bool = True,
) -> Operation:
    """restore_after=True (default): the existing behavior -- migrate every
    live-migrated VM back to this node and power the shutdown-in-place ones
    back on, once the patch/reboot has verified clean. False: leave the
    node empty -- nothing migrates back, nothing restarts, maintenance
    mode just ends. Full maintenance needs the option to either return
    VMs back or leave the node empty when it's done."""
    cluster, target = _load_context(db, node)
    op = create_operation(
        db, "maintenance.run", cluster_id=cluster.id, node_id=node.id,
        context={"node": node.name, "force_reboot": force_reboot, "restore_after": restore_after}, created_by=actor,
    )

    reasons: list[str] = [
        "this run includes real Stage W4 package patching -- its own preflight (wrapper reachability, "
        "disk headroom, plan) is checked again when the patch stage actually runs, not here; reboot only "
        "happens if W4's post-apply verification determines one is required."
        + (" A reboot will happen regardless, per the 'reboot anyway' option." if force_reboot else ""),
        (
            "once patched (and rebooted, if needed) every VM moved off will be migrated back to this node "
            "and anything shut down in place will start back up"
            if restore_after
            else "once patched (and rebooted, if needed) the node will be left empty -- nothing migrates back "
            "or restarts, per the 'leave the node empty' option"
        ),
    ]
    blocking_rules: list[str] = []

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
    # Stopped workloads don't need to move for a reboot -- they aren't
    # consuming compute on this node either way. Only flagged (never
    # blocking) when their disk is node-local, since that data still
    # physically depends on this node even while the guest is stopped.
    stopped_no_action = []
    stopped_relocation_required = []

    client = None
    try:
        client, _cred = build_pve_client(db, target)
    except Exception as exc:
        reasons.append(f"could not build read client to plan maintenance: {exc}")

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
                    note_planned_move(simulated_added_bytes, wl, destination_node.id)
                    currently_on_shared = is_currently_on_shared_storage(client, node, wl, db)
                    current_storage = current_storage_name(client, node, wl)
                    effective_pref = wl.storage_preference or get_cluster_storage_preference(db, cluster.id)
                    storage_rec = recommend_storage_for_candidate(db, destination_node.id, currently_on_shared, storage_preference=effective_pref)
                    migrate_plan.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "destination_node_id": str(destination_node.id), "destination_node": destination_node.name,
                        "destination_storage_id": storage_rec["id"] if storage_rec else None,
                        "currently_on_shared": currently_on_shared,
                        "current_storage": current_storage,
                        "storage_preference": effective_pref,
                        # Full ranked field, not just the pick -- lets the
                        # approval preview offer an alternate destination,
                        # so everything it's going to do has options
                        # selectable right there.
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
                    # Can't live-migrate -- graceful shutdown-in-place instead,
                    # unless the downtime_tolerance floor itself blocks it.
                    if wl.downtime_tolerance == "low":
                        blocked_workloads.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                                                   "reasons": ["cannot live-migrate AND tagged downtime_tolerance='low' -- no safe automated path"]})
                    else:
                        # No UNBLOCKED candidate (commonly headroom, a
                        # resource judgment call rather than a hard fact) --
                        # defaults to shutdown in place, but every candidate
                        # is still offered so a human can opt this into a
                        # real move instead -- every workload should be
                        # displayed for choice of handling regardless of
                        # headroom.
                        fallback = ranked[0] if ranked else None
                        item = {
                            "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                            "reasons": fallback.blocking_reasons if fallback else ["no other online node in this cluster"],
                            "included": False,
                            # Distinguishes this from a stopped_relocation_required
                            # row in the same combined UI list -- this one IS
                            # currently running, and the UI should note
                            # when a workload is stopped vs. running.
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
                    stopped_relocation_required.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                                                         "reasons": [f"could not determine storage locality ({exc}) -- manual review recommended"],
                                                         "included": False})
                    continue
                if on_shared:
                    stopped_no_action.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                                               "reasons": ["stopped, and its disk is on shared storage -- no action needed for this reboot"]})
                else:
                    # Never required -- a stopped guest costs this node
                    # nothing either way, and for a plain temporary reboot
                    # (restore_after=True) it just sits here and comes back
                    # with the node regardless. But every workload still
                    # gets a real, visible destination choice, not just the
                    # running ones -- "leave it" is itself a placement
                    # decision, it just shouldn't be an invisible default:
                    # a full evacuation still requires some type of
                    # action/placement for every workload on the host, not
                    # just the ones with an easy auto-pick.
                    ranked = recommend_destinations(db, client, wl, candidates_all, simulated_added_bytes=simulated_added_bytes)
                    ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
                    # Fall back to the best-scored candidate even if flagged
                    # -- see evacuation_workflow.py's identical fallback.
                    top = next((c for c in ranked if not c.blocked), None) or (ranked[0] if ranked else None)
                    item = {
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": (
                            ["stopped, with its disk on node-local storage -- this node is being left empty "
                             "afterward, so relocate it too if it shouldn't stay stranded here; optional"]
                            if not restore_after else
                            ["stopped, with its disk on node-local storage -- fine to leave as-is for this "
                             "temporary reboot (it stays put and comes back with the node), relocate only if "
                             "you want to for some other reason"]
                        ),
                        "included": False,
                        "currently_running": False,
                        "currently_on_shared": False,
                    }
                    if top is not None:
                        destination_node = db.query(Node).filter(Node.id == top.node_id).one()
                        storage_rec = recommend_storage_for_candidate(db, destination_node.id, False)
                        item.update({
                            "destination_node_id": str(destination_node.id), "destination_node": destination_node.name,
                            "destination_storage_id": storage_rec["id"] if storage_rec else None,
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
        "node": node.name,
        "migrate_plan": migrate_plan,
        "shutdown_plan": shutdown_plan,
        "stopped_relocation_required": stopped_relocation_required,
        "original_node_id": str(node.id),
        "force_reboot": force_reboot,
        "restore_after": restore_after,
    }

    if blocking_rules:
        return enter_stage(db, op, status="blocked", stage="preflight", dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, context=context, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="preflight", dry_run_result=dry_run_result, context=context, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_maintenance(db: Session, operation_id) -> Operation:
    """Resumable across every stage -- progress is tracked in op.context so
    a restart picks up exactly where it left off rather than repeating
    (or skipping) anything."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise MaintenanceWorkflowError(f"operation {operation_id} not found")

    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _load_context(db, node)
    ctx = op.context or {}
    migrate_plan = ctx.get("migrate_plan", [])
    shutdown_plan = ctx.get("shutdown_plan", [])
    # Opt-in stopped-VM relocations (see dry_run_maintenance) -- only
    # offered at all when restore_after=False, and only actually moved if
    # a human explicitly marked one "included" before approving.
    included_stopped = [item for item in ctx.get("stopped_relocation_required", []) if item.get("included")]
    completed_migrations = list(ctx.get("completed_migrations", []))
    completed_shutdowns = list(ctx.get("completed_shutdowns", []))
    completed_stopped = list(ctx.get("completed_stopped_relocation_ids", []))
    restored = list(ctx.get("restored_workload_ids", []))
    force_reboot = bool(ctx.get("force_reboot", False))
    restore_after = bool(ctx.get("restore_after", True))
    # Snapshotted ONCE, the very first time this operation runs (see the
    # "approved" branch below) -- captures whether the node was already in
    # a standalone, persistent maintenance_mode (via node.enter_maintenance)
    # before this run's own patch cycle touches the flag. Without this, a
    # Full Maintenance run on a node someone had deliberately put into
    # maintenance mode would silently clear that back to False at the end,
    # as if the node had never been asked to stay there -- a real gap
    # found live: running Full Maintenance on a node already in
    # maintenance mode from an earlier node.enter_maintenance brought it
    # out of maintenance mode with no one asking for that.
    was_already_in_maintenance = ctx.get("was_already_in_maintenance")

    def save_progress():
        op.context = {
            **ctx, "migrate_plan": migrate_plan, "shutdown_plan": shutdown_plan,
            "completed_migrations": completed_migrations, "completed_shutdowns": completed_shutdowns,
            "completed_stopped_relocation_ids": completed_stopped,
            "restored_workload_ids": restored,
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
                # 23400s (6.5h) matches this operation type's own RQ job
                # timeout (21600s/6h) plus a 30min margin -- the prior
                # 7200s (2h) TTL was far short of the documented 6h max
                # runtime for a real full-maintenance run.
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="maintenance.run", ttl_seconds=23400)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            if was_already_in_maintenance is None:
                was_already_in_maintenance = node.maintenance_mode
                ctx = {**ctx, "was_already_in_maintenance": was_already_in_maintenance}
                op.context = ctx
                db.commit()
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            op = enter_stage(db, op, status="evacuating", stage="evacuate")

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

                # Same opt-out as node_maintenance_workflow.py's identical
                # branch -- a workload with a perfectly good auto-found
                # destination can still be told "just shut it down
                # instead": some workloads can stay in place and just be
                # powered down during updates, then brought back up once
                # updates/reboot are complete. Tracked in
                # completed_shutdowns so restore_placement's existing
                # "power back on anything left stopped here" loop for
                # completed_shutdowns picks it up with zero new plumbing.
                if item.get("transport") == "shutdown_in_place":
                    child = lifecycle_workflow.dry_run_shutdown(
                        db, workload, actor=f"maintenance.run:{op.id}",
                        parent_operation_id=op.id, correlation_id=op.correlation_id,
                    )
                    if child.status == "blocked":
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                    child = lifecycle_workflow.approve(db, child, approved_by=f"maintenance.run:{op.id}")
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
                from .models import Storage
                destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none() if item.get("destination_storage_id") else None
                try:
                    child = dry_run_migration(
                        db, workload, destination_node, actor=f"maintenance.run:{op.id}",
                        destination_storage=destination_storage, transport=item.get("transport", "live"),
                        confirm_override_headroom=bool(item.get("manually_set")),
                        parent_operation_id=op.id, correlation_id=op.correlation_id,
                    )
                except MigrationWorkflowError as exc:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"could not plan migration for vmid {item['vmid']}: {exc}")
                if child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                child = approve_migration(db, child, approved_by=f"maintenance.run:{op.id}")
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
                # see node_maintenance_workflow.py's identical branch for why
                # a flagged (e.g. headroom-short) destination is honored once
                # a human has explicitly included this row: every workload
                # should be displayed for choice of handling regardless of
                # headroom. Note: if restore_after=True, this
                # VM stays on the destination it moved to rather than being
                # migrated back afterward -- restore_placement below only
                # restarts a workload it finds STOPPED, and this one won't
                # be; a known, non-breaking gap, not a crash.
                if item.get("included") and item.get("destination_node_id"):
                    destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                    if destination_node is None:
                        completed_shutdowns.append(item["workload_id"])
                        save_progress()
                        continue
                    destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none() if item.get("destination_storage_id") else None
                    try:
                        child = dry_run_migration(
                            db, workload, destination_node, actor=f"maintenance.run:{op.id}",
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
                    child = approve_migration(db, child, approved_by=f"maintenance.run:{op.id}")
                    child = execute_migration(db, child.id)
                    if child.status != "completed":
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"migration for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                    completed_shutdowns.append(item["workload_id"])
                    save_progress()
                    continue

                child = lifecycle_workflow.dry_run_shutdown(
                    db, workload, actor=f"maintenance.run:{op.id}",
                    parent_operation_id=op.id, correlation_id=op.correlation_id,
                )
                if child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                child = lifecycle_workflow.approve(db, child, approved_by=f"maintenance.run:{op.id}")
                child = lifecycle_workflow.execute_lifecycle_action(db, child.id)
                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"graceful shutdown for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                completed_shutdowns.append(item["workload_id"])
                save_progress()

            # Opt-in stopped-VM relocations -- only ever populated when
            # restore_after=False (leaving this node empty for good), and
            # only actually moved here if a human explicitly included one
            # before approving: a stopped workload doesn't need to move
            # but should still have the option to, in case maintenance
            # includes removing the host from the cluster.
            # Same reconcile-before-create pattern as the loops above,
            # kept separate rather than merged in to avoid touching their
            # already-proven logic.
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
                from .models import Storage
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
                        child = approve_migration(db, child, approved_by=f"maintenance.run:{op.id}")
                    if child.status not in TERMINAL_STATUSES:
                        child = execute_migration(db, child.id)
                else:
                    try:
                        child = dry_run_migration(
                            db, workload, destination_node, actor=f"maintenance.run:{op.id}",
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
                    child = approve_migration(db, child, approved_by=f"maintenance.run:{op.id}")
                    child = execute_migration(db, child.id)

                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"relocation for stopped vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                completed_stopped.append(item["workload_id"])
                save_progress()

            op = enter_stage(db, op, status="verifying", stage="verify_evacuation")

        if op.status == "verifying" and op.stage == "verify_evacuation":
            cancelled = check_cancel_requested(db, op)
            if cancelled:
                return cancelled
            still_running = running_on_node_live(db, target, node)
            if still_running:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{still_running} workload(s) still running on {node.name} after evacuation -- stopping before reboot")
            # host.update/host.reboot both hard-require maintenance_mode now
            # (see node_maintenance_workflow.py) -- this run satisfies that
            # itself, around its own already-proven evacuate/patch/reboot/
            # restore sequence, rather than requiring a separate standalone
            # enter_maintenance first. Only takes ownership of the flag (and
            # only clears it again at the end, below) when the node wasn't
            # already in maintenance mode for some other reason -- otherwise
            # this would clobber a standalone node.enter_maintenance's own
            # reason/since/by, and then silently cancel it at the end.
            if not was_already_in_maintenance:
                node.maintenance_mode = True
                node.maintenance_mode_since = now_utc()
                node.maintenance_reason = "maintenance.run patch cycle"
                node.maintenance_mode_by = op.created_by
                db.commit()
            op = enter_stage(db, op, status="evacuating", stage="patch")

        if op.stage == "patch" and op.status != "completed":
            patch_op_id = ctx.get("patch_operation_id")
            if not patch_op_id:
                patch_child = host_update_workflow.dry_run_host_update(
                    db, node, actor=f"maintenance.run:{op.id}",
                    parent_operation_id=op.id, correlation_id=op.correlation_id,
                )
                if patch_child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(
                        db, op,
                        blocking_safety_rules=(patch_child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"],
                        actor="system",
                    )
                patch_child = host_update_workflow.approve(db, patch_child, approved_by=f"maintenance.run:{op.id}")
                op.context = {**(op.context or {}), "patch_operation_id": str(patch_child.id)}
                db.commit()
                patch_op_id = str(patch_child.id)

            patch_result = host_update_workflow.execute_host_update(db, patch_op_id)
            if patch_result.status != "completed":
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error=f"host patching did not complete (status={patch_result.status}): {patch_result.error}",
                )

            reboot_required = bool((patch_result.verification_result or {}).get("reboot_required"))
            # reboot_required stays the real, honest W4 determination --
            # force_reboot is a separate override so the audit trail always
            # shows what actually needed a reboot vs. what was rebooted
            # anyway by request.
            op.context = {**(op.context or {}), "patch_operation_id": patch_op_id, "reboot_required": reboot_required}
            db.commit()

            if reboot_required or force_reboot:
                op = enter_stage(db, op, status="evacuating", stage="reboot")
            else:
                # No reboot needed -- packages applied cleanly without one
                # (matches host_update_workflow's own independent
                # determination, not a guess made here) and nobody asked
                # for one anyway. Skip straight to restoring placement.
                op = enter_stage(db, op, status="verifying", stage="restore_placement")

        if op.stage == "reboot" and op.status != "completed":
            # Delegate the entire reboot lifecycle to Stage W5 as a real
            # child operation -- same reasoning as evacuate's child
            # migrations: reuse the proven component exactly, don't
            # reimplement its safety checks here.
            reboot_op_id = ctx.get("reboot_operation_id")
            if not reboot_op_id:
                reboot_child = reboot_workflow.dry_run_reboot(
                    db, node, actor=f"maintenance.run:{op.id}",
                    parent_operation_id=op.id, correlation_id=op.correlation_id,
                )
                if reboot_child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=(reboot_child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                reboot_child = reboot_workflow.approve(db, reboot_child, approved_by=f"maintenance.run:{op.id}")
                op.context = {**(op.context or {}), "reboot_operation_id": str(reboot_child.id)}
                db.commit()
                reboot_op_id = str(reboot_child.id)
            op = enter_stage(db, op, status="executing", stage="reboot")
            reboot_result = reboot_workflow.execute_reboot(db, reboot_op_id)
            if reboot_result.status != "completed":
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"host reboot did not complete (status={reboot_result.status}): {reboot_result.error}")
            op = enter_stage(db, op, status="verifying", stage="verify_node")
            op = enter_stage(db, op, status="verifying", stage="restore_placement")

        if op.stage == "restore_placement":
            if restore_after:
                for wid in completed_migrations:
                    if wid in restored:
                        continue
                    cancelled = check_cancel_requested(db, op)
                    if cancelled:
                        return cancelled
                    workload = db.query(Workload).filter(Workload.id == wid).one_or_none()
                    if workload is None or workload.is_missing:
                        restored.append(wid)
                        save_progress()
                        continue
                    try:
                        child = dry_run_migration(
                            db, workload, node, actor=f"maintenance.run:{op.id}",
                            parent_operation_id=op.id, correlation_id=op.correlation_id,
                        )
                    except MigrationWorkflowError as exc:
                        # Non-fatal: leave it on its evacuation destination,
                        # note it and move on -- restoring placement is a nicety,
                        # not a safety requirement.
                        restored.append(wid)
                        save_progress()
                        continue
                    if child.status == "awaiting_approval":
                        child = approve_migration(db, child, approved_by=f"maintenance.run:{op.id}")
                        execute_migration(db, child.id)
                    restored.append(wid)
                    save_progress()

                for wid in completed_shutdowns:
                    if wid in restored:
                        continue
                    workload = db.query(Workload).filter(Workload.id == wid).one_or_none()
                    if workload is not None and not workload.is_missing and workload.status == "stopped":
                        child = lifecycle_workflow.dry_run_start(
                            db, workload, actor=f"maintenance.run:{op.id}",
                            parent_operation_id=op.id, correlation_id=op.correlation_id,
                        )
                        if child.status == "awaiting_approval":
                            child = lifecycle_workflow.approve(db, child, approved_by=f"maintenance.run:{op.id}")
                            lifecycle_workflow.execute_lifecycle_action(db, child.id)
                    restored.append(wid)
                    save_progress()
            # restore_after=False -- leave the node empty. Live-migrated
            # VMs stay on their evacuation destinations and
            # shutdown-in-place VMs stay off; nothing in either plan is
            # touched here at all.

            op = enter_stage(db, op, status="verifying", stage="verify_workloads")

        if op.stage == "verify_workloads":
            # Only clear the flag if this run was the one that set it --
            # a node someone deliberately put into maintenance mode before
            # this run started stays in maintenance mode afterward too.
            if not was_already_in_maintenance:
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
        return fail_operation(db, op, error=f"unexpected error at stage {op.stage}: {exc}")
