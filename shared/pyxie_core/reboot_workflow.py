"""Stage W5: controlled host reboot with pre-checks and post-reboot
verification. A node that fails to return STOPS the workflow and requires
operator intervention -- there is no automatic IPMI/Redfish power cycle
anywhere in this module, by design, matching the original staged plan.

Every PVE client built in this module explicitly avoids the node being
rebooted (see credentials.resolve_pve_endpoint / avoid_node_id below), so
PyXie keeps talking to the rest of the cluster through another healthy
member while this node is offline, instead of losing all cluster
visibility for the duration (the earlier, now-fixed behavior, when
PyXie's only configured endpoint happened to be the node under
maintenance). This still depends on at least one other node being online
and having a known management_ip on record (populated by discovery from
PVE's own /cluster/status) -- the monitoring loop below tolerates
connection errors as "still down, keep waiting" regardless of the reason,
so a total-cluster-unreachable edge case degrades to blind waiting rather
than a hard failure, same as before.
"""

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .operation_log import log_stage
from .maintenance import _quorum_after_removal
from .models import Cluster, Node, PveTarget, PveTask, Operation, Workload
from .operations_engine import (
    OperationError,
    approve_operation,
    block_operation,
    create_operation,
    enter_stage,
    fail_operation,
)
from .pve_client import PveConnectionError, PveTlsError
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback

NODE_OFFLINE_CONFIRM_TIMEOUT_SECONDS = 120
NODE_RETURN_TIMEOUT_SECONDS = 900
POLL_INTERVAL_SECONDS = 5


class RebootWorkflowError(Exception):
    pass


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return cluster, target


def dry_run_reboot(db: Session, node: Node, *, actor: str, parent_operation_id=None, correlation_id=None) -> Operation:
    cluster, target = _load_context(db, node)
    op = create_operation(
        db, "host.reboot", cluster_id=cluster.id, node_id=node.id,
        parent_operation_id=parent_operation_id, correlation_id=correlation_id,
        context={"node": node.name}, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []

    quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
    if quorum.get("evaluated") and not quorum["quorum_holds"]:
        reasons.append("rebooting this node would leave the cluster with insufficient quorum margin")
        blocking_rules.append("SAFE-QUORUM-001")

    resident = db.query(Workload).filter(Workload.node_id == node.id, Workload.is_missing.is_(False)).all()
    running_resident = [w for w in resident if w.status == "running"]
    if running_resident:
        reasons.append(
            f"{len(running_resident)} workload(s) still running on this node -- evacuate first "
            f"(Stage W2 node evacuation / Stage W3 shutdown), reboot assumes an already-evacuated node"
        )
        blocking_rules.append("SAFE-ROLLBACK-001")

    active_tasks = db.query(PveTask).filter(PveTask.node_id == node.id, PveTask.status == "running").count()
    if active_tasks:
        reasons.append(f"{active_tasks} active PVE task(s) on this node -- wait for them to finish first")
        blocking_rules.append("SAFE-BACKUP-001")

    if node.pending_updates is None or node.pending_updates == 0:
        reasons.append("no pending updates recorded for this node -- confirm a reboot is actually needed")

    other_online_nodes = db.query(Node).filter(
        Node.cluster_id == cluster.id, Node.id != node.id, Node.is_missing.is_(False),
        Node.status == "online", Node.management_ip.isnot(None),
    ).count()
    if other_online_nodes == 0:
        reasons.append(
            "no other online cluster member with a known management IP is on record -- PyXie will not be able "
            "to fail over its API connection away from this node during the reboot, and will fall back to "
            "waiting blindly until connectivity returns rather than having live visibility into the cluster"
        )

    dry_run_result = {
        "node": node.name, "eligible": len(blocking_rules) == 0,
        "reasons": reasons, "blocking_safety_rules": blocking_rules,
        "pending_updates": node.pending_updates,
    }

    if blocking_rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _node_status_tolerant(db: Session, target: PveTarget, node_name: str, *, avoid_node_id=None):
    """Returns (reachable: bool, online: bool, cluster_status: list|None).
    Connection errors are treated as 'we can't tell, keep waiting', not as
    a hard failure -- that's the whole point during a reboot window."""
    try:
        client, _cred = build_pve_client(db, target, avoid_node_id=avoid_node_id)
    except Exception:
        return False, False, None
    try:
        with client:
            status = client.cluster_status()
    except (PveConnectionError, PveTlsError):
        return False, False, None
    except Exception:
        return False, False, None
    entry = next((e for e in status if e.get("type") == "node" and e.get("name") == node_name), None)
    return True, bool(entry and entry.get("online")), status


def execute_reboot(db: Session, operation_id) -> Operation:
    """Resumable. The offline-confirm and wait-for-return phases are safe
    to resume from scratch after a worker restart -- they just re-poll live
    state, they don't depend on anything held in memory."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise RebootWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "monitoring", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _load_context(db, node)

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            log_stage(op.id, f"Approved. Re-checking {node.name}: maintenance mode, reachability, quorum, no guests left on it.")
            if not node.maintenance_mode:
                # Previewing reboot eligibility never required this (dry_run_reboot
                # has no such gate) -- only actually rebooting does.
                return block_operation(db, op, blocking_safety_rules=["SAFE-MAINTMODE-001"], actor="system")
            reachable, online, _ = _node_status_tolerant(db, target, node.name, avoid_node_id=node.id)
            if not reachable:
                return block_operation(db, op, blocking_safety_rules=["SAFE-QUORUM-001"], actor="system")
            # Re-check quorum with LIVE state right before actually rebooting --
            # dry-run's quorum check can be stale by the time a human approves
            # (another node could have gone offline in the meantime); this is
            # not the same check as `reachable` above, which only confirms
            # THIS node is still up.
            quorum = _quorum_after_removal(db, cluster, target, node.name, avoid_node_id=node.id)
            if quorum.get("evaluated") and not quorum["quorum_holds"]:
                return block_operation(db, op, blocking_safety_rules=["SAFE-QUORUM-001"], actor="system")
            still_resident = db.query(Workload).filter(
                Workload.node_id == node.id, Workload.is_missing.is_(False), Workload.status == "running"
            ).count()
            if still_resident:
                return block_operation(db, op, blocking_safety_rules=["SAFE-ROLLBACK-001"], actor="system")

            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.reboot", ttl_seconds=1800)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        # POST /nodes/{node}/status returns null per PVE's own schema --
        # there is never a real PVE task/UPID for a node reboot, unlike
        # every other write in this codebase. The old code stored that
        # null into op.pve_upid and used `not op.pve_upid` as the "already
        # sent" resumability gate -- but null is ALSO the value before
        # sending, so a crash between issuing the real reboot command and
        # committing the stage transition to "monitoring" left nothing to
        # distinguish "already sent, don't send again" from "never sent" on
        # resume, and a resumed worker could issue a second real reboot
        # command to a node already rebooting. Use an explicit context flag
        # instead, set in the SAME enter_stage call/commit as the stage
        # transition so there's no window where the command was sent but
        # that fact wasn't yet durable.
        if op.status == "executing" and not (op.context or {}).get("reboot_command_sent"):
            maintenance_creds = load_pve_credentials(db, target, "maintenance", avoid_node_id=node.id)
            log_stage(op.id, f"Checks passed. Sending the reboot command to {node.name}.")
            with PveMaintenanceClient(maintenance_creds) as write_client:
                write_client.reboot_node(node.name)
            op = enter_stage(
                db, op, status="monitoring", stage="monitoring",
                context={**(op.context or {}), "reboot_command_sent": True},
            )

        if op.status == "monitoring":
            # Phase 1: confirm the node actually goes offline (proves the
            # reboot command really took effect, not just that PVE accepted
            # the request).
            deadline = time.monotonic() + NODE_OFFLINE_CONFIRM_TIMEOUT_SECONDS
            went_offline = False
            phase_start = time.monotonic()
            last_note = phase_start
            log_stage(op.id, f"Reboot command sent. Waiting for {node.name} to go offline.")
            while time.monotonic() < deadline:
                reachable, online, _ = _node_status_tolerant(db, target, node.name, avoid_node_id=node.id)
                if not reachable or not online:
                    went_offline = True
                    log_stage(op.id, f"{node.name} is offline (after {int(time.monotonic() - phase_start)}s).")
                    break
                if time.monotonic() - last_note >= 15:
                    last_note = time.monotonic()
                    log_stage(op.id, f"Still online, waiting for it to shut down ({int(last_note - phase_start)}s).")
                time.sleep(POLL_INTERVAL_SECONDS)
            if not went_offline:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"node {node.name} never went offline within {NODE_OFFLINE_CONFIRM_TIMEOUT_SECONDS}s of the reboot command -- requires operator investigation")

            # Phase 2: wait for it to come back. No automatic power-cycle if
            # it doesn't -- this stops and waits for a human.
            deadline = time.monotonic() + NODE_RETURN_TIMEOUT_SECONDS
            came_back = False
            phase_start = time.monotonic()
            last_note = phase_start
            log_stage(op.id, f"Waiting for {node.name} to boot and rejoin the cluster.")
            while time.monotonic() < deadline:
                reachable, online, _ = _node_status_tolerant(db, target, node.name, avoid_node_id=node.id)
                if reachable and online:
                    came_back = True
                    log_stage(op.id, f"{node.name} is back online (after {int(time.monotonic() - phase_start)}s).")
                    break
                if time.monotonic() - last_note >= 15:
                    last_note = time.monotonic()
                    log_stage(op.id, f"Still waiting for {node.name} ({int(last_note - phase_start)}s).")
                time.sleep(POLL_INTERVAL_SECONDS)
            if not came_back:
                release_locks_for_operation(db, op.id)
                op.rollback_classification = rollback.IRREVERSIBLE
                db.commit()
                return fail_operation(
                    db, op,
                    error=f"node {node.name} did not return within {NODE_RETURN_TIMEOUT_SECONDS}s of going offline -- "
                          f"STOPPING here, no automatic power-cycle attempted. Requires operator intervention.",
                )
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            log_stage(op.id, f"Verifying {node.name}: cluster membership, quorum, uptime.")
            reachable, online, cluster_status = _node_status_tolerant(db, target, node.name, avoid_node_id=node.id)
            quorate_entry = next((e for e in (cluster_status or []) if e.get("type") == "cluster"), None)

            # A node that just rejoined the cluster can take a few seconds
            # before pvestatd's cluster-wide view of it has kernel/uptime
            # populated -- retry briefly rather than accepting a single
            # transient failure and silently reporting no data (which also
            # skips the uptime-sanity check below). If every attempt fails,
            # record what actually happened instead of discarding it.
            fresh_node_status = None
            status_fetch_error = None
            for attempt in range(3):
                try:
                    client, _cred = build_pve_client(db, target, avoid_node_id=node.id)
                    with client:
                        fresh_node_status = client.node_status(node.name)
                    status_fetch_error = None
                    break
                except Exception as exc:
                    status_fetch_error = str(exc)
                    if attempt < 2:
                        time.sleep(3)

            verification = {
                "reachable": reachable, "online": online,
                "quorate": bool(quorate_entry and quorate_entry.get("quorate")) if quorate_entry else None,
                "kernel_version": (fresh_node_status or {}).get("kversion"),
                "uptime_seconds": (fresh_node_status or {}).get("uptime"),
            }
            if status_fetch_error:
                verification["status_fetch_error"] = status_fetch_error
            op.verification_result = verification
            db.commit()

            if not (reachable and online):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"node reachable={reachable} online={online} -- verification failed")
            if quorate_entry is not None and not quorate_entry.get("quorate"):
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error="cluster is not quorate after the node returned")
            if verification["uptime_seconds"] is not None and verification["uptime_seconds"] > NODE_RETURN_TIMEOUT_SECONDS:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"node uptime ({verification['uptime_seconds']}s) suggests it may not have actually rebooted")

            log_stage(op.id, f"Verification passed: online, quorate, uptime {verification['uptime_seconds']}s, kernel {verification['kernel_version']}.")
            release_locks_for_operation(db, op.id)
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:
                pass
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.IRREVERSIBLE)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
