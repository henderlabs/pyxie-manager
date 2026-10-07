"""Rightsizing 'Apply' workflow (workload.resize): change a workload's
vCPU/RAM allocation, driven from a Recommendations-page suggestion (or a
manually-edited value -- the operator can always override the suggested
number before applying).

Cores/memory aren't hot-pluggable on a normally-configured guest, so this
always shuts the guest down first if it's currently running, reconfigures,
then starts it back up -- never leaves a workload in a different power
state than it started in. Same Safety Contract as every other operation
type: dry-run -> approve -> revalidate -> lock -> execute -> monitor each
PVE task -> read the config back from PVE (not just trust the PUT
response) -> verify -> audit.
"""

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .labels import vm_label
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
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient, SelfProtectionError, TaskTimeoutError
from . import rollback

MIN_VCPU = 1
MIN_MEMORY_MB = 512


class ResizeWorkflowError(Exception):
    pass


def _load_context(db, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def dry_run_resize(db, workload: Workload, *, new_cores: int, new_memory_mb: int, actor: str, correlation_id=None) -> Operation:
    if workload.type != "vm":
        raise ResizeWorkflowError("only VM (qemu) resize is implemented, not LXC")
    if new_cores < MIN_VCPU:
        raise ResizeWorkflowError(f"cannot set below {MIN_VCPU} vCPU")
    if new_memory_mb < MIN_MEMORY_MB:
        raise ResizeWorkflowError(f"cannot set below {MIN_MEMORY_MB}MB memory")

    node, cluster, target = _load_context(db, workload)
    was_running = workload.status == "running"
    context = {
        "node_id": str(node.id), "node": node.name, "vmid": workload.vmid,
        "current_cores": workload.cpu_cores, "current_memory_bytes": workload.memory_bytes,
        "new_cores": new_cores, "new_memory_mb": new_memory_mb,
        "was_running": was_running,
    }

    op = create_operation(
        db, "workload.resize", cluster_id=cluster.id, node_id=node.id, workload_id=workload.id,
        correlation_id=correlation_id, context=context, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []

    if workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}' -- not safe to resize right now")
        blocking_rules.append("SAFE-MIGRATE-001")

    if was_running and workload.downtime_tolerance == "low":
        reasons.append(
            "workload is tagged downtime_tolerance='low' (cannot tolerate downtime) -- "
            "this will power it off to apply the change, requires explicit review"
        )
        blocking_rules.append("SAFE-DOWNTIME-001")

    if was_running and workload.ha_state:
        reasons.append(f"HA state: {workload.ha_state} -- PVE's HA manager may restart this guest during the power-off window")
        blocking_rules.append("SAFE-HA-001")

    dry_run_result = {
        "vmid": workload.vmid, "workload_name": workload.name, "node": node.name,
        "current_cores": workload.cpu_cores, "new_cores": new_cores,
        "current_memory_bytes": workload.memory_bytes, "new_memory_bytes": new_memory_mb * 1024 * 1024,
        "will_power_cycle": was_running,
        "eligible": len(blocking_rules) == 0, "reasons": reasons, "blocking_safety_rules": blocking_rules,
    }

    if blocking_rules:
        return enter_stage(
            db, op, status="blocked", stage="dry_run",
            dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor,
        )
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_resize(db, operation_id) -> Operation:
    """Resumable. Stage order: revalidating -> shutting_down (skipped if
    the guest was already stopped) -> reconfiguring -> starting_up (skipped
    if it wasn't running before) -> verifying -> completed."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise ResizeWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "shutting_down", "reconfiguring", "starting_up", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    node, cluster, target = _load_context(db, workload)
    ctx = op.context or {}
    was_running = bool(ctx.get("was_running"))
    new_cores = ctx["new_cores"]
    new_memory_mb = ctx["new_memory_mb"]

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
            # was_running was captured at dry-run time and is what governs
            # whether we power-cycle at all -- if live state has since
            # DIVERGED from that (someone started/stopped it by hand in the
            # meantime), that's drift a human should see, not paper over.
            if (live.get("status") == "running") != was_running:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-MIGRATE-001"], actor="system")

            try:
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason="workload.resize")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")

            next_status = "shutting_down" if was_running else "reconfiguring"
            op = enter_stage(db, op, status=next_status, stage=next_status)

        maintenance_creds = load_pve_credentials(db, target, "maintenance")

        if op.status == "shutting_down":
            if not op.pve_upid:
                with PveMaintenanceClient(maintenance_creds) as write_client:
                    upid = write_client.shutdown_vm(node.name, workload.vmid, timeout=120)
                op.pve_upid = upid
                db.commit()
            with PveMaintenanceClient(maintenance_creds) as write_client:
                task_result = write_client.wait_for_task(node.name, op.pve_upid, timeout=180)
            if not task_result.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"shutdown did not succeed: {task_result.raw}")
            op.pve_upid = None  # clear so reconfiguring/starting_up each track their own UPID cleanly
            db.commit()
            op = enter_stage(db, op, status="reconfiguring", stage="reconfiguring")

        if op.status == "reconfiguring":
            with PveMaintenanceClient(maintenance_creds) as write_client:
                write_client.set_vm_config(node.name, workload.vmid, cores=new_cores, memory_mb=new_memory_mb)
            next_status = "starting_up" if was_running else "verifying"
            op = enter_stage(db, op, status=next_status, stage=next_status)

        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(maintenance_creds) as write_client:
                    upid = write_client.start_vm(node.name, workload.vmid)
                op.pve_upid = upid
                db.commit()
            with PveMaintenanceClient(maintenance_creds) as write_client:
                task_result = write_client.wait_for_task(node.name, op.pve_upid, timeout=180)
            if not task_result.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error=f"restart after resize did not succeed: {task_result.raw} -- "
                          "the config change was already applied, the guest just didn't come back up on its own",
                )
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                live_vms = read_client.qemu_list(node.name)
                live_config = read_client.qemu_config(node.name, workload.vmid)
            live = next((v for v in live_vms if int(v.get("vmid", -1)) == workload.vmid), None)
            op.verification_result = {
                "vm_state": live, "cores": live_config.get("cores"), "memory": live_config.get("memory"),
            }
            db.commit()

            expect_status = "running" if was_running else "stopped"
            problems = []
            if live is None or live.get("status") != expect_status:
                problems.append(f"status={live.get('status') if live else 'missing'}, expected '{expect_status}'")
            if int(live_config.get("cores") or 1) != new_cores:
                problems.append(f"cores={live_config.get('cores')}, expected {new_cores}")
            if int(live_config.get("memory") or 0) != new_memory_mb:
                problems.append(f"memory={live_config.get('memory')}MB, expected {new_memory_mb}MB")
            if problems:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error="read-back after resize didn't match: " + "; ".join(problems))

            release_locks_for_operation(db, op.id)
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
