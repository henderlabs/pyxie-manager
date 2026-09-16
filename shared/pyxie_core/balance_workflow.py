"""cluster.rebalance -- "Balance Load" as one reviewable/editable plan,
same Safety Contract shape as node.enter_maintenance/maintenance.run/
node.evacuate, instead of its old flow (propose N moves via
/api/recommendations/balance-load-plan, apply each individually with no
single approval and no cancel). Reuses _placement_recommendations()'s
scoring pass unchanged -- this is a thin wrapper around it that packages
the result as a migrate_plan and executes it the same way every other
batch migration workflow does.

Not tied to one node (node_ids may span several, or be None for
cluster-wide) -- Operation.node_id stays null; instead every node that
appears as a source or destination anywhere in the plan gets its own
lock, so this can't race with e.g. someone else starting maintenance on
one of those nodes mid-rebalance, and so two rebalances can't overlap on
the same node. Each child vm.live_migrate then locks its own source/
destination again as part of its own Safety Contract -- not a conflict,
since acquire_lock() already treats a lock held by an ancestor operation
as the same actor, not contention.
"""

from sqlalchemy.orm import Session

from .locks import LockContention, acquire_lock, release_locks_for_operation
from .migration_workflow import MigrationWorkflowError, approve as approve_migration, dry_run_migration, execute_migration
from .models import Node, Operation, Storage, Workload
from .operations_engine import (
    OperationError,
    approve_operation,
    block_operation,
    check_cancel_requested,
    create_operation,
    enter_stage,
    fail_operation,
)
from .recommendations import _placement_recommendations


class BalanceWorkflowError(Exception):
    pass


def dry_run_balance(db: Session, *, actor: str, node_ids: list | None = None) -> Operation:
    op = create_operation(
        db, "cluster.rebalance",
        context={"node_ids": [str(n) for n in node_ids] if node_ids else None},
        created_by=actor,
    )

    source_node_ids = set(node_ids) if node_ids else None
    recs = _placement_recommendations(db, source_node_ids=source_node_ids)

    migrate_plan = []
    for r in recs:
        wl = db.query(Workload).filter(Workload.id == r["object_id"]).one_or_none()
        if wl is None or wl.is_missing:
            continue
        ev = r["evidence"]
        migrate_plan.append({
            "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
            "destination_node_id": ev["suggested_node_id"], "destination_node": ev["suggested_node"],
            "destination_storage_id": ev["suggested_storage"]["id"] if ev.get("suggested_storage") else None,
            "currently_on_shared": ev.get("currently_on_shared", False),
            "current_storage": ev.get("current_storage"),
            "storage_preference": ev.get("storage_preference"),
            "candidates": ev.get("candidates", []),
            # Per-VM live vs. shutdown/migrate/power-on choice, editable in
            # the same plan preview as the destination override -- default
            # to live so existing behavior doesn't change unless picked
            # (live migration can be too costly time-wise with node-local
            # storage, so an operator may want to switch this per item).
            "transport": "live",
        })

    reasons = (
        [f"{len(migrate_plan)} workload(s) would move to improve cluster balance -- each move is still reviewed "
         "and approved individually before it happens, same as every other migration here."]
        if migrate_plan else
        ["nothing scores meaningfully better elsewhere right now -- already balanced."]
    )
    dry_run_result = {
        "node": None, "reasons": reasons, "eligible": True, "blocking_safety_rules": [],
        "migrate_plan": migrate_plan,
    }
    ctx = {**(op.context or {}), "migrate_plan": migrate_plan}
    if not migrate_plan:
        op = enter_stage(db, op, status="dry_run", stage="preflight", dry_run_result=dry_run_result, context=ctx, actor=actor)
        return enter_stage(db, op, status="completed", stage="audit", actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="preflight", dry_run_result=dry_run_result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_balance(db: Session, operation_id) -> Operation:
    """Resumable -- progress tracked in op.context exactly like every
    other batch migration workflow's own evacuate stage."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise BalanceWorkflowError(f"operation {operation_id} not found")

    ctx = op.context or {}
    migrate_plan = ctx.get("migrate_plan", [])
    completed_migrations = list(ctx.get("completed_migrations", []))

    def save_progress():
        op.context = {**ctx, "migrate_plan": migrate_plan, "completed_migrations": completed_migrations}
        db.commit()

    try:
        if op.status == "approved":
            involved_node_ids = set()
            for item in migrate_plan:
                wl = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if wl is not None:
                    involved_node_ids.add(wl.node_id)
                involved_node_ids.add(item["destination_node_id"])
            try:
                for node_id in involved_node_ids:
                    # 12600s (3.5h) matches this operation type's own RQ
                    # job timeout (10800s/3h) plus a 30min margin, same
                    # reasoning as node.evacuate.
                    acquire_lock(db, resource_type="node", resource_id=node_id, operation_id=op.id, reason="cluster.rebalance", ttl_seconds=12600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            op = enter_stage(db, op, status="evacuating", stage="evacuating")

        if op.status == "evacuating":
            for item in migrate_plan:
                if item["workload_id"] in completed_migrations:
                    continue
                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled
                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                if workload is None or workload.is_missing or destination_node is None:
                    completed_migrations.append(item["workload_id"])
                    save_progress()
                    continue
                destination_storage = (
                    db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none()
                    if item.get("destination_storage_id") else None
                )
                try:
                    child = dry_run_migration(
                        db, workload, destination_node, actor=f"cluster.rebalance:{op.id}",
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
                child = approve_migration(db, child, approved_by=f"cluster.rebalance:{op.id}")
                child = execute_migration(db, child.id)
                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"migration for vmid {item['vmid']} did not complete (status={child.status}): {child.error}")
                completed_migrations.append(item["workload_id"])
                save_progress()

            release_locks_for_operation(db, op.id)
            # No `return` here -- fall through to the `verifying` block below
            # in this same call, exactly like evacuation_workflow.py does.
            # This used to `return` right after the transition, which meant
            # the operation only ever finished if something re-invoked this
            # function afterward -- and nothing does except a worker restart's
            # resume_inflight_operations(). Every real "stuck in verifying"
            # incident (2026-09-13, three times) was this bug.
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            release_locks_for_operation(db, op.id)
            return enter_stage(db, op, status="completed", stage="audit")

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status}, stage={op.stage})")

    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error at stage {op.stage}: {exc}")
