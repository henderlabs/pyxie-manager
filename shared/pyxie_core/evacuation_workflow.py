"""Stage W2: controlled node evacuation, one workload at a time, with a full
cluster reassessment between each move -- "the plan is guidance, current
reality always wins."

Deliberately scoped to LIVE MIGRATION ONLY, matching the original staged
plan: a workload that can't be live-migrated (needs shutdown, or is
otherwise blocked) makes the dry-run refuse to plan an evacuation at all
-- W3 (shutdown/start) and combining the two is explicitly W6's job, not
this one's. Every individual VM move inside an evacuation reuses
migration_workflow.py's own dry_run_migration/approve/execute_migration
functions directly -- same eligibility checks, same Safety Contract, same
audit envelope as a standalone W1 migration; nothing is duplicated or
special-cased for being "part of an evacuation" except that each child
migration is auto-approved once the evacuation PLAN itself has been
approved by a human (matching the original diagram: Approve + LOCK PLAN
happens once, not per VM).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .locks import LockContention, acquire_lock, release_locks_for_operation
from .maintenance import _quorum_after_removal
from .migration_workflow import (
    MigrationWorkflowError,
    approve as approve_migration,
    dry_run_migration,
    execute_migration,
)
from .models import Cluster, Node, Operation, PveTarget, Storage, Workload
from .operations_engine import (
    TERMINAL_STATUSES,
    OperationError,
    block_operation,
    check_cancel_requested,
    create_operation,
    enter_stage,
    fail_operation,
)
from .placement import current_storage_name, is_currently_on_shared_storage, rank_with_simulated_load, recommend_destinations, recommend_storage_for_candidate
from .discovery import build_pve_client


class EvacuationWorkflowError(Exception):
    pass


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return cluster, target


def dry_run_evacuation(db: Session, node: Node, *, actor: str) -> Operation:
    cluster, target = _load_context(db, node)

    op = create_operation(
        db, "node.evacuate", cluster_id=cluster.id, node_id=node.id,
        context={"node": node.name}, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []

    quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
    if quorum.get("evaluated") and not quorum["quorum_holds"]:
        reasons.append("evacuating this node would leave the cluster with insufficient quorum margin")
        blocking_rules.append("SAFE-QUORUM-001")

    workloads = db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False)).all()
    running_vms = [w for w in workloads if w.type == "vm" and w.status == "running"]
    running_lxc = [w for w in workloads if w.type == "lxc" and w.status == "running"]
    stopped_workloads = [w for w in workloads if w.status != "running"]

    client = None
    try:
        client, _cred = build_pve_client(db, target)
    except Exception as exc:
        reasons.append(f"could not build read client to plan evacuation: {exc}")

    plan = []
    unmigratable = []
    # Stopped workloads never need to be running for this node to be
    # evacuated -- they aren't consuming any compute on it right now. What
    # actually matters is whether their DATA still physically depends on
    # this node: a stopped guest fully on shared storage needs no action at
    # all (its config + disk are already reachable from anywhere), while one
    # with a node-local-only disk stays tied to this node's storage even
    # though it isn't running. Neither case blocks the evacuation by
    # itself -- per the original design mistake this replaces, a stopped
    # workload used to hard-block the ENTIRE run, which was wrong.
    stopped_no_action = []
    stopped_relocation_required = []
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
                ranked = recommend_destinations(db, client, wl, candidates_all, simulated_added_bytes=simulated_added_bytes)
                ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
                top = next((c for c in ranked if not c.blocked), None)
                if top is None:
                    unmigratable.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": ranked[0].blocking_reasons if ranked else ["no other online node in this cluster"],
                    })
                    continue
                destination_node = db.query(Node).filter(Node.id == top.node_id).one()
                simulated_added_bytes[destination_node.id] = simulated_added_bytes.get(destination_node.id, 0) + (wl.memory_bytes or 0)
                currently_on_shared = is_currently_on_shared_storage(client, node, wl, db)
                current_storage = current_storage_name(client, node, wl)
                storage_rec = recommend_storage_for_candidate(db, destination_node.id, currently_on_shared)
                storage_choice = storage_rec["id"] if storage_rec else None
                plan.append({
                    "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                    "destination_node_id": str(destination_node.id), "destination_node": destination_node.name,
                    "destination_storage_id": storage_choice,
                    "currently_on_shared": currently_on_shared,
                    "current_storage": current_storage,
                    # Full ranked field, not just the pick -- lets the
                    # approval preview offer an alternate destination, so
                    # everything it's going to do has options selectable
                    # right there in the preview.
                    "candidates": [
                        {
                            "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score,
                            "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                        }
                        for c in ranked
                    ],
                    # Per-VM live vs. shutdown/migrate/power-on choice,
                    # editable in the same plan preview as the destination
                    # override -- defaults to live so existing behavior
                    # doesn't change unless picked (live migration can be
                    # too costly time-wise with node-local storage).
                    "transport": "live",
                })

            for wl in stopped_workloads:
                try:
                    on_shared = is_currently_on_shared_storage(client, node, wl, db)
                except Exception as exc:
                    stopped_relocation_required.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": [f"could not determine storage locality ({exc}) -- manual review recommended"],
                        "included": False,
                        "currently_running": False,
                    })
                    continue
                if on_shared:
                    stopped_no_action.append({
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": ["stopped, and its disk is on shared storage -- no action needed to evacuate this node"],
                    })
                else:
                    # Never required for a temporary evacuation (the whole
                    # point of "stopped" is it isn't costing this node
                    # anything right now) -- but if the node is actually
                    # leaving the cluster for good, this workload's disk
                    # would be stranded on it, so a destination is computed
                    # here too and offered as an opt-in move
                    # ("included": False by default), same editable-plan
                    # pattern as a running workload, just with no live
                    # option -- it's already off, so this is always an
                    # offline (config+disk copy only) relocation. A stopped
                    # workload doesn't NEED to move but should still have
                    # the option to, in case maintenance includes removing
                    # the host from the cluster.
                    ranked = recommend_destinations(db, client, wl, candidates_all, simulated_added_bytes=simulated_added_bytes)
                    ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
                    # Fall back to the best-scored candidate even if it's
                    # flagged (headroom is a snapshot, not a hard fact) --
                    # this is already an opt-in, human-reviewed row, so it
                    # shouldn't be left with NO destination choice just
                    # because nothing scored clean; every workload should
                    # be displayed for choice of handling regardless of
                    # headroom.
                    top = next((c for c in ranked if not c.blocked), None) or (ranked[0] if ranked else None)
                    item = {
                        "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                        "reasons": ["stopped, but its disk is on node-local storage -- config/data stays tied to this "
                                    "node even though it isn't running; only needed if this node is leaving service "
                                    "for good, optional otherwise"],
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
                        # A destination picked here doesn't cost this
                        # stopped VM's memory against later candidates the
                        # way a running one's does -- it's opt-in and not
                        # actually moving unless approved, so it shouldn't
                        # silently eat into headroom real running-VM moves
                        # still need in this same dry run.
                    stopped_relocation_required.append(item)
    else:
        for wl in running_vms:
            unmigratable.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name, "reasons": ["no read client available"]})
        for wl in stopped_workloads:
            stopped_relocation_required.append({
                "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                "reasons": ["no read client available -- could not classify"], "included": False,
                "currently_running": False,
            })

    for wl in running_lxc:
        unmigratable.append({
            "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
            "reasons": ["type=lxc status=running -- live migration not attempted by PyXie yet; needs Stage W3 (shutdown/start) or manual handling"],
        })

    if unmigratable:
        reasons.append(
            f"{len(unmigratable)} workload(s) cannot be evacuated by live migration alone "
            f"(need Stage W3 shutdown handling or manual action, not yet available): "
            + ", ".join(f"{u['name'] or u['vmid']}" for u in unmigratable)
        )
        blocking_rules.append("SAFE-MIGRATE-001")

    dry_run_result = {
        "node": node.name,
        "workload_count": len(workloads),
        "plan": plan,
        "unmigratable": unmigratable,
        "stopped_no_action": stopped_no_action,
        "stopped_relocation_required": stopped_relocation_required,
        "eligible": len(blocking_rules) == 0,
        "reasons": reasons,
        "blocking_safety_rules": blocking_rules,
    }

    # stopped_relocation_required rides along in context (not just
    # dry_run_result) so an opt-in "included" toggle set before approval
    # survives to execute_evacuation() -- dry_run_result alone is a
    # snapshot for display, context is what execution actually reads.
    context = {"node": node.name, "plan": plan, "stopped_relocation_required": stopped_relocation_required}

    if blocking_rules:
        return enter_stage(
            db, op, status="blocked", stage="dry_run",
            dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules,
            context=context, actor=actor,
        )

    op = enter_stage(
        db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result,
        context=context, actor=actor,
    )
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    from .operations_engine import approve_operation
    return approve_operation(db, op, approved_by=approved_by)


def execute_evacuation(db: Session, operation_id) -> Operation:
    """Resumable. One workload at a time, full cluster reassessment between
    each -- if a worker/process restarts mid-evacuation, this picks back up
    from op.context['completed_workload_ids'] rather than re-migrating
    anything already moved, and rather than blindly continuing without
    re-checking the ones still to go."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise EvacuationWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "evacuating", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _load_context(db, node)
    ctx = op.context or {}
    plan = ctx.get("plan", [])
    completed = list(ctx.get("completed_workload_ids", []))
    # Opt-in stopped-VM relocations (see dry_run_evacuation) -- only the
    # ones a human explicitly marked "included" before approving actually
    # get moved; the rest were shown for visibility only and are left
    # exactly where they are, stopped, on this node.
    included_stopped = [item for item in ctx.get("stopped_relocation_required", []) if item.get("included")]
    completed_stopped = list(ctx.get("completed_stopped_relocation_ids", []))

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            # Re-check quorum with LIVE state right before evacuating -- the
            # dry-run's check can be stale by the time a human approves the
            # plan (another node could have gone offline in the meantime).
            quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
            if quorum.get("evaluated") and not quorum["quorum_holds"]:
                return block_operation(db, op, blocking_safety_rules=["SAFE-QUORUM-001"], actor="system")
            try:
                # 12600s (3.5h) matches this operation type's own RQ job
                # timeout (10800s/3h, api/app/routers/operations.py's
                # _JOB_TIMEOUT_BY_TYPE) plus the same 30min margin used
                # elsewhere -- the DEFAULT_TTL of 1h this call previously
                # used (no override) could expire while a real,
                # multi-workload evacuation was still legitimately running.
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="node.evacuate", ttl_seconds=12600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="evacuating", stage="evacuating")

        if op.status == "evacuating":
            for item in plan:
                if item["workload_id"] in completed:
                    continue

                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled

                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if workload is None or workload.is_missing:
                    completed.append(item["workload_id"])  # gone from inventory -- nothing to evacuate
                    op.context = {**ctx, "plan": plan, "completed_workload_ids": completed}
                    db.commit()
                    continue

                destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                if destination_node is None:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(
                        db, op,
                        error=f"destination node for vmid {item['vmid']} disappeared from inventory mid-evacuation "
                              f"({len(completed)}/{len(plan)} completed before this)",
                    )
                destination_storage = None
                if item.get("destination_storage_id"):
                    destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none()

                # Reconcile against an EXISTING child for this workload before
                # ever spawning a new one -- if a worker crashed mid-evacuation
                # after this child was created (whether it went on to complete,
                # is still genuinely in flight, or already failed/blocked),
                # blindly calling dry_run_migration again here would create a
                # second, duplicate child operation for the same workload.
                # That new attempt can't cause an actual double PVE mutation
                # (the workload-level resource lock a still-running original
                # child holds prevents that), but it produces exactly the
                # confusing, needs-manual-untangling state this reconciliation
                # is meant to avoid -- so look for prior work first, always.
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
                    # Succeeded before a crash interrupted this loop's own
                    # bookkeeping (the completed_workload_ids commit below
                    # never ran) -- consume it, don't redo the migration.
                    child = existing_child
                elif existing_child is not None and existing_child.status in ("failed", "blocked"):
                    # A prior attempt for this exact workload already
                    # concluded unsuccessfully -- surface that outcome rather
                    # than silently retrying with a fresh child, which would
                    # bury the original failure in an unrelated duplicate.
                    # A human should decide whether conditions changed enough
                    # to warrant a genuine retry.
                    release_locks_for_operation(db, op.id)
                    if existing_child.status == "blocked":
                        return block_operation(
                            db, op,
                            blocking_safety_rules=(existing_child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"],
                            actor="system",
                        )
                    return fail_operation(
                        db, op,
                        error=f"migration for vmid {item['vmid']} (operation {existing_child.id}) previously "
                              f"failed: {existing_child.error} -- {len(completed)}/{len(plan)} completed before "
                              f"this, not retried automatically",
                    )
                elif existing_child is not None:
                    # Still genuinely in flight from before the crash (any
                    # non-terminal status) -- resume THAT operation instead
                    # of creating a duplicate.
                    child = existing_child
                    if child.status == "awaiting_approval":
                        child = approve_migration(db, child, approved_by=f"node.evacuate:{op.id}")
                    if child.status not in TERMINAL_STATUSES:
                        child = execute_migration(db, child.id)
                else:
                    # No prior child for this workload -- fresh dry-run,
                    # right now, reassessing cluster state between every
                    # move rather than trusting the plan computed before any
                    # of the earlier moves happened.
                    try:
                        child = dry_run_migration(
                            db, workload, destination_node, actor=f"node.evacuate:{op.id}",
                            destination_storage=destination_storage, transport=item.get("transport", "live"),
                            confirm_override_headroom=bool(item.get("manually_set")),
                            parent_operation_id=op.id, correlation_id=op.correlation_id,
                        )
                    except MigrationWorkflowError as exc:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(
                            db, op,
                            error=f"could not plan migration for vmid {item['vmid']}: {exc} "
                                  f"({len(completed)}/{len(plan)} completed before this)",
                        )

                    if child.status == "blocked":
                        release_locks_for_operation(db, op.id)
                        return block_operation(
                            db, op,
                            blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"],
                            actor="system",
                        )

                    child = approve_migration(db, child, approved_by=f"node.evacuate:{op.id}")
                    child = execute_migration(db, child.id)

                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(
                        db, op,
                        error=f"migration for vmid {item['vmid']} did not complete (status={child.status}, "
                              f"error={child.error}) -- {len(completed)}/{len(plan)} completed before this, "
                              f"evacuation stopped rather than continuing past a failure",
                    )

                completed.append(item["workload_id"])
                op.context = {**ctx, "plan": plan, "completed_workload_ids": completed}
                db.commit()

            # Opt-in stopped-VM relocations, same reconcile-before-create
            # pattern as the plan loop above but kept as its own loop
            # rather than merged into it -- these were never required for
            # this evacuation to succeed (nothing here blocks or fails the
            # whole run the way an unmigratable RUNNING workload would),
            # they're purely "since we're already moving things off this
            # node, also relocate this stopped one's disk so it isn't
            # stranded here" -- a stopped workload should still have the
            # option to move if maintenance includes removing the host
            # from the cluster.
            for item in included_stopped:
                if item["workload_id"] in completed_stopped:
                    continue

                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled

                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if workload is None or workload.is_missing:
                    completed_stopped.append(item["workload_id"])
                    op.context = {**ctx, "plan": plan, "completed_workload_ids": completed, "completed_stopped_relocation_ids": completed_stopped}
                    db.commit()
                    continue

                destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                if destination_node is None:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(
                        db, op,
                        error=f"destination node for stopped vmid {item['vmid']} disappeared from inventory mid-evacuation",
                    )
                destination_storage = None
                if item.get("destination_storage_id"):
                    destination_storage = db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none()

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
                        return block_operation(
                            db, op,
                            blocking_safety_rules=(existing_child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"],
                            actor="system",
                        )
                    return fail_operation(
                        db, op,
                        error=f"relocation for stopped vmid {item['vmid']} (operation {existing_child.id}) previously "
                              f"failed: {existing_child.error}",
                    )
                elif existing_child is not None:
                    child = existing_child
                    if child.status == "awaiting_approval":
                        child = approve_migration(db, child, approved_by=f"node.evacuate:{op.id}")
                    if child.status not in TERMINAL_STATUSES:
                        child = execute_migration(db, child.id)
                else:
                    try:
                        child = dry_run_migration(
                            db, workload, destination_node, actor=f"node.evacuate:{op.id}",
                            destination_storage=destination_storage, transport="offline",
                            confirm_override_headroom=True,
                            parent_operation_id=op.id, correlation_id=op.correlation_id,
                        )
                    except MigrationWorkflowError as exc:
                        release_locks_for_operation(db, op.id)
                        return fail_operation(db, op, error=f"could not plan relocation for stopped vmid {item['vmid']}: {exc}")

                    if child.status == "blocked":
                        release_locks_for_operation(db, op.id)
                        return block_operation(
                            db, op,
                            blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"],
                            actor="system",
                        )

                    child = approve_migration(db, child, approved_by=f"node.evacuate:{op.id}")
                    child = execute_migration(db, child.id)

                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(
                        db, op,
                        error=f"relocation for stopped vmid {item['vmid']} did not complete (status={child.status}, "
                              f"error={child.error})",
                    )

                completed_stopped.append(item["workload_id"])
                op.context = {**ctx, "plan": plan, "completed_workload_ids": completed, "completed_stopped_relocation_ids": completed_stopped}
                db.commit()

            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            remaining = [
                item for item in plan
                if item["workload_id"] not in completed
            ]
            still_present = []
            for item in plan:
                wl = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if wl and not wl.is_missing and str(wl.node_id) == str(node.id):
                    still_present.append(item["vmid"])
            for item in included_stopped:
                wl = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if wl and not wl.is_missing and str(wl.node_id) == str(node.id):
                    still_present.append(item["vmid"])

            op.verification_result = {
                "planned": len(plan), "completed": len(completed),
                "stopped_relocations_planned": len(included_stopped), "stopped_relocations_completed": len(completed_stopped),
                "still_on_source_node": still_present,
            }
            db.commit()

            release_locks_for_operation(db, op.id)
            if still_present:
                return fail_operation(
                    db, op,
                    error=f"evacuation ran but {len(still_present)} planned workload(s) still show on {node.name} "
                          f"after read-back verification: vmids {still_present}",
                )
            return enter_stage(db, op, status="completed", stage="audit")

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except Exception as exc:  # noqa: BLE001 -- always record, never silently drop
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error ({len(completed)}/{len(plan)} completed before this): {exc}")
