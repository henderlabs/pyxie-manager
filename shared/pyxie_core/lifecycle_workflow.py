"""Stage W3: graceful guest shutdown/start, the separate higher-risk
force_stop, and reboot -- the start/stop/restart buttons on the Workloads
page. Four operation types sharing one workflow module since
they're all single-workload, single-node, no-destination actions -- unlike
migration/evacuation there's no "where does it go" decision, just "is this
safe to do right now." Reboot is the odd one out state-wise: shutdown/
start/force_stop each flip status running<->stopped, but a guest reboot
never leaves 'running' from PVE's own perspective (it's an ACPI signal the
guest handles internally) -- everywhere below that matters, reboot is
grouped with whichever side of that flip it actually behaves like.

workload.force_stop is NEVER invoked automatically as a shutdown-timeout
fallback -- see pve_write_client.shutdown_vm's own docstring. It's a
separate operation_type_id that a human must explicitly choose and approve,
same Safety Contract as everything else, not a retry path off a failed
shutdown. The same is true of reboot's own timeout: it never escalates to
force_stop either.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import already_in_desired_state, vm_label
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
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, SelfProtectionError, TaskTimeoutError, self_vmid_from_env
from . import rollback

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 120


class LifecycleWorkflowError(Exception):
    pass


def _load_context(db: Session, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def _dry_run(
    db: Session, workload: Workload, operation_type_id: str, *, actor: str,
    require_status: str, shutdown_timeout: int | None = None, justification: str | None = None,
    parent_operation_id=None, correlation_id=None,
) -> Operation:
    if workload.type != "vm":
        raise LifecycleWorkflowError(f"only VM (qemu) lifecycle actions are implemented, not LXC")

    node, cluster, target = _load_context(db, workload)
    context = {"node_id": str(node.id), "node": node.name, "vmid": workload.vmid}
    if shutdown_timeout is not None:
        context["shutdown_timeout_seconds"] = shutdown_timeout
    if justification:
        context["justification"] = justification

    op = create_operation(
        db, operation_type_id, cluster_id=cluster.id, node_id=node.id, workload_id=workload.id,
        parent_operation_id=parent_operation_id, correlation_id=correlation_id,
        context=context, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []

    if workload.status != require_status:
        reasons.append(f"workload status is '{workload.status}', expected '{require_status}' for this action")
        blocking_rules.append("SAFE-MIGRATE-001")

    if operation_type_id in ("workload.shutdown", "workload.force_stop", "workload.reboot"):
        if workload.downtime_tolerance == "low":
            reasons.append(
                "workload is tagged downtime_tolerance='low' (cannot tolerate downtime) -- "
                "this action deliberately takes it down, requires explicit review"
            )
            blocking_rules.append("SAFE-DOWNTIME-001")
        if workload.ha_state:
            reasons.append(f"HA state: {workload.ha_state} -- PVE's HA manager may restart this guest automatically")
            blocking_rules.append("SAFE-HA-001")

    # Reboot is deliberately exempt from pve_write_client's self-protection
    # guard (see reboot_vm's own docstring) -- but it should never be
    # SILENTLY exempt: exempted with a visible warning, not a blocking
    # rule. shutdown/force_stop never reach here already blocked --
    # pve_write_client refuses those unconditionally regardless of what
    # this dry-run says.
    if operation_type_id == "workload.reboot" and str(workload.vmid) == (self_vmid_from_env() or ""):
        reasons.append(
            "⚠ this is the VM running PyXie Manager itself -- the API/UI will be briefly unreachable "
            "during this reboot, and this very operation will be interrupted mid-flight (it resumes "
            "automatically once the guest comes back up and pyxie-worker restarts)"
        )

    dry_run_result = {
        "vmid": workload.vmid, "workload_name": workload.name, "node": node.name,
        "eligible": len(blocking_rules) == 0, "reasons": reasons, "blocking_safety_rules": blocking_rules,
    }
    if shutdown_timeout is not None:
        dry_run_result["shutdown_timeout_seconds"] = shutdown_timeout

    if blocking_rules:
        return enter_stage(
            db, op, status="blocked", stage="dry_run",
            dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor,
        )
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def dry_run_shutdown(db: Session, workload: Workload, *, actor: str, timeout_seconds: int = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS, parent_operation_id=None, correlation_id=None) -> Operation:
    return _dry_run(db, workload, "workload.shutdown", actor=actor, require_status="running", shutdown_timeout=timeout_seconds, parent_operation_id=parent_operation_id, correlation_id=correlation_id)


def dry_run_start(db: Session, workload: Workload, *, actor: str, parent_operation_id=None, correlation_id=None) -> Operation:
    return _dry_run(db, workload, "workload.start", actor=actor, require_status="stopped", parent_operation_id=parent_operation_id, correlation_id=correlation_id)


def dry_run_reboot(db: Session, workload: Workload, *, actor: str, timeout_seconds: int = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS, parent_operation_id=None, correlation_id=None) -> Operation:
    return _dry_run(db, workload, "workload.reboot", actor=actor, require_status="running", shutdown_timeout=timeout_seconds, parent_operation_id=parent_operation_id, correlation_id=correlation_id)


def dry_run_force_stop(db: Session, workload: Workload, *, actor: str, justification: str, parent_operation_id=None, correlation_id=None) -> Operation:
    if not justification or not justification.strip():
        raise LifecycleWorkflowError("a justification is required to force-stop a workload")
    return _dry_run(db, workload, "workload.force_stop", actor=actor, require_status="running", justification=justification, parent_operation_id=parent_operation_id, correlation_id=correlation_id)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_lifecycle_action(db: Session, operation_id) -> Operation:
    """Resumable. Handles all three operation types -- shutdown, start,
    force_stop -- by dispatching on op.operation_type_id, since they share
    everything except which PVE call and which expected end-state."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise LifecycleWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "monitoring", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = op.context or {}

    expect_status_after = {
        "workload.shutdown": "stopped",
        "workload.start": "running",
        "workload.force_stop": "stopped",
        "workload.reboot": "running",
    }[op.operation_type_id]

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                live_vms = read_client.qemu_list(node.name)
            live = next((v for v in live_vms if int(v.get("vmid", -1)) == workload.vmid), None)
            if live is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"{vm_label(workload)} is no longer listed on {node.name}")
            if already_in_desired_state(op.operation_type_id, live.get("status")):
                # Something else already put it there (typically an earlier step of the same maintenance run, or a plan
                # built from inventory that was a minute stale). Nothing to do: that is success, not a blocker.
                op.verification_result = {"vm_state": live, "note": "already in the requested state; nothing to do"}
                db.commit()
                release_locks_for_operation(db, op.id)
                return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.AUTO_REVERSIBLE)
            expected_before = "running" if op.operation_type_id in ("workload.shutdown", "workload.force_stop", "workload.reboot") else "stopped"
            if live.get("status") != expected_before:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-MIGRATE-001"], actor="system")

            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason=op.operation_type_id)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing" and not op.pve_upid:
            maintenance_creds = load_pve_credentials(db, target, "maintenance")
            with PveMaintenanceClient(maintenance_creds) as write_client:
                if op.operation_type_id == "workload.shutdown":
                    upid = write_client.shutdown_vm(node.name, workload.vmid, timeout=ctx.get("shutdown_timeout_seconds", DEFAULT_SHUTDOWN_TIMEOUT_SECONDS))
                elif op.operation_type_id == "workload.start":
                    upid = write_client.start_vm(node.name, workload.vmid)
                elif op.operation_type_id == "workload.reboot":
                    upid = write_client.reboot_vm(node.name, workload.vmid, timeout=ctx.get("shutdown_timeout_seconds", DEFAULT_SHUTDOWN_TIMEOUT_SECONDS))
                else:
                    upid = write_client.force_stop_vm(node.name, workload.vmid)
            op.pve_upid = upid
            db.commit()
            op = enter_stage(db, op, status="monitoring", stage="monitoring", pve_upid=upid)

        if op.status in ("executing", "monitoring") and op.pve_upid:
            if op.status != "monitoring":
                op = enter_stage(db, op, status="monitoring", stage="monitoring")
            maintenance_creds = load_pve_credentials(db, target, "maintenance")
            # Shutdown needs enough wall-clock budget to cover the guest's
            # own ACPI timeout, not just PVE's task-poll interval.
            wait_timeout = max(ctx.get("shutdown_timeout_seconds", 0) + 60, 300)
            with PveMaintenanceClient(maintenance_creds) as write_client:
                task_result = write_client.wait_for_task(node.name, op.pve_upid, timeout=wait_timeout)
            op.pve_task_result = task_result.raw
            db.commit()
            if not task_result.succeeded:
                release_locks_for_operation(db, op.id)
                classification = rollback.MANUAL_REVERSIBLE if op.operation_type_id in ("workload.shutdown", "workload.reboot") else rollback.UNKNOWN
                return _fail_with_classification(db, op, task_result, classification)
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                live_vms = read_client.qemu_list(node.name)
            live = next((v for v in live_vms if int(v.get("vmid", -1)) == workload.vmid), None)
            op.verification_result = {"vm_state": live}
            db.commit()

            if live is None or live.get("status") != expect_status_after:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error=f"PVE task reported success but read-back shows status="
                          f"{live.get('status') if live else 'missing'}, expected '{expect_status_after}'",
                )

            release_locks_for_operation(db, op.id)
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:
                pass
            classification = rollback.AUTO_REVERSIBLE if op.operation_type_id != "workload.force_stop" else rollback.IRREVERSIBLE
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=classification)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")


def _fail_with_classification(db: Session, op: Operation, task_result, classification: str) -> Operation:
    op.rollback_classification = classification
    db.commit()
    return fail_operation(
        db, op,
        error=f"PVE task {op.pve_upid} did not succeed: {task_result.raw}"
        + (" -- guest did not shut down within its timeout; force_stop is a separate, deliberately-invoked "
           "operation, never an automatic fallback."
           if op.operation_type_id in ("workload.shutdown", "workload.reboot") else ""),
    )
