"""Job functions for the 'polling' and 'operations' queues.

Phase 0 registered jobs only for the polling workload class. This pass adds
findings/recommendation/protection evaluation to the SAME queue (still just
polling -- these are all read-only, low-cost passes over already-collected
data) rather than introducing a separate 'analysis' consumer prematurely.

Stage W0/W1 gives the 'operations' queue its first-ever consumer:
execute_operation_job, which dispatches to the per-operation-type workflow
module. This is the ONLY job function in this file that can result in a PVE
write -- everything above it stays read-only, same as before.
"""

import os
from datetime import datetime, timezone

import redis
from rq import Queue

from pyxie_core.db import SessionLocal
from pyxie_core.findings import evaluate_findings
from pyxie_core.metrics import prune_old_metrics
from pyxie_core.models import (
    InternalJobRun,
    PveTarget,
    Provider,
    ProtectionTarget,
)
from pyxie_core.protection import sync_pbs_protection
from pyxie_core.recommendations import generate_recommendations

_conn = redis.from_url(os.environ["REDIS_URL"])
operations_queue = Queue("operations", connection=_conn)


def _run_logged(job_name: str, fn):
    db = SessionLocal()
    run = InternalJobRun(job_name=job_name, started_at=datetime.now(timezone.utc), status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    result_summary = None
    try:
        result = fn(db)
        result_summary = result if isinstance(result, dict) else {"result": str(result)}
        run.status = "success"
        run.result_summary = result_summary
    except Exception as e:  # noqa: BLE001 -- always record the failure, never crash the worker
        db.rollback()
        run.status = "failed"
        run.error = str(e)
    finally:
        run.ended_at = datetime.now(timezone.utc)
        db.commit()
        db.close()
    # read into a local before the session closed and expired the instance
    return result_summary


def discover_all_targets():
    from pyxie_core.discovery import run_discovery

    def _job(db):
        targets = (
            db.query(PveTarget)
            .join(Provider, PveTarget.provider_id == Provider.id)
            .filter(Provider.enabled.is_(True))
            .all()
        )
        results = {}
        for target in targets:
            results[str(target.id)] = run_discovery(db, target, actor="worker")
        return results

    return _run_logged("discovery", _job)


def sync_protection_targets():
    def _job(db):
        targets = (
            db.query(ProtectionTarget)
            .join(Provider, ProtectionTarget.provider_id == Provider.id)
            .filter(Provider.enabled.is_(True), Provider.provider_type == "pbs")
            .all()
        )
        results = {}
        for target in targets:
            results[str(target.id)] = sync_pbs_protection(db, target, actor="worker")
        return results

    return _run_logged("protection_sync", _job)


def evaluate_findings_job():
    return _run_logged("findings_evaluation", lambda db: evaluate_findings(db))


def generate_recommendations_job():
    return _run_logged("recommendation_generation", lambda db: generate_recommendations(db))


def prune_metrics_job():
    return _run_logged("metrics_pruning", lambda db: {"deleted": prune_old_metrics(db)})


def execute_operation_job(operation_id: str):
    """Dispatches one Operation row to its operation_type's workflow module.
    Resumable by construction: this can be (and, after a worker restart via
    resume_inflight_operations(), will be) called again for the same
    operation_id -- the workflow module itself decides where to pick up
    based on the row's own persisted state, never from any in-memory state
    this job might have held before a crash.
    """
    from pyxie_core.models import Operation

    def _job(db):
        op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
        if op is None:
            return {"error": f"operation {operation_id} not found"}
        if op.operation_type_id == "vm.live_migrate":
            from pyxie_core.migration_workflow import execute_migration
            result = execute_migration(db, operation_id)
        elif op.operation_type_id == "node.evacuate":
            from pyxie_core.evacuation_workflow import execute_evacuation
            result = execute_evacuation(db, operation_id)
        elif op.operation_type_id in ("workload.shutdown", "workload.start", "workload.force_stop", "workload.reboot"):
            from pyxie_core.lifecycle_workflow import execute_lifecycle_action
            result = execute_lifecycle_action(db, operation_id)
        elif op.operation_type_id == "workload.resize":
            from pyxie_core.resize_workflow import execute_resize
            result = execute_resize(db, operation_id)
        elif op.operation_type_id == "host.reboot":
            from pyxie_core.reboot_workflow import execute_reboot
            result = execute_reboot(db, operation_id)
        elif op.operation_type_id == "host.update":
            from pyxie_core.host_update_workflow import execute_host_update
            result = execute_host_update(db, operation_id)
        elif op.operation_type_id == "maintenance.run":
            from pyxie_core.maintenance_workflow import execute_maintenance
            result = execute_maintenance(db, operation_id)
        elif op.operation_type_id == "node.enter_maintenance":
            from pyxie_core.node_maintenance_workflow import execute_enter_maintenance
            result = execute_enter_maintenance(db, operation_id)
        elif op.operation_type_id == "node.exit_maintenance":
            from pyxie_core.node_maintenance_workflow import execute_exit_maintenance
            result = execute_exit_maintenance(db, operation_id)
        elif op.operation_type_id == "cluster.rebalance":
            from pyxie_core.balance_workflow import execute_balance
            result = execute_balance(db, operation_id)
        elif op.operation_type_id == "protection.backup_membership":
            from pyxie_core.protection_workflow import execute_backup_membership
            result = execute_backup_membership(db, operation_id)
        elif op.operation_type_id == "workload.network_vlan_change":
            from pyxie_core.network_workflow import execute_vlan_change
            result = execute_vlan_change(db, operation_id)
        else:
            return {"error": f"no workflow implementation for operation type {op.operation_type_id!r}"}
        return {"operation_id": str(result.id), "status": result.status, "stage": result.stage}

    return _run_logged(f"execute_operation:{operation_id}", _job)


def resume_inflight_operations():
    """Called once at worker startup: find every operation that was
    mid-flight (not waiting on human approval, not terminal) when the
    process last stopped -- crash, restart, redeploy, docker compose down --
    and re-enqueue it. This is what makes 'operations persist state in
    PostgreSQL and survive worker/API/container restarts' actually true
    rather than just a design intention.
    """
    from pyxie_core.operations_engine import get_resumable_operations

    # Long-running types (evacuation runs many sequential migrations;
    # maintenance runs evacuation + a reboot wait on top of that; reboot
    # alone can wait up to NODE_RETURN_TIMEOUT_SECONDS) get generous
    # job_timeouts so RQ doesn't kill them mid-operation.
    RESUMABLE_TYPES = {
        # kept in sync with api/app/routers/operations.py's _JOB_TIMEOUT_BY_TYPE
        "vm.live_migrate": 16200,
        "node.evacuate": 10800,
        "workload.shutdown": 600,
        "workload.start": 300,
        "workload.force_stop": 300,
        "workload.reboot": 600,
        "host.reboot": 1800,
        "host.update": 3900,
        "maintenance.run": 21600,
        "node.enter_maintenance": 10800,
        "node.exit_maintenance": 1800,
        "cluster.rebalance": 10800,  # same evacuation shape as node.evacuate
        "protection.backup_membership": 120,  # synchronous PVE config write, no task to wait on
        "workload.network_vlan_change": 120,  # synchronous PVE config write, no task to wait on -- same shape
    }

    def _job(db):
        resumable = get_resumable_operations(db)
        for op in resumable:
            timeout = RESUMABLE_TYPES.get(op.operation_type_id)
            if timeout:
                operations_queue.enqueue("worker.jobs.execute_operation_job", str(op.id), job_timeout=timeout)
        return {"resumed": [str(op.id) for op in resumable]}

    return _run_logged("resume_inflight_operations", _job)


def run_all():
    """The single scheduled entry point: discovery -> protection -> findings
    -> recommendations, in that dependency order, each independently logged
    and each tolerant of the others failing.
    """
    discover_all_targets()
    sync_protection_targets()
    evaluate_findings_job()
    generate_recommendations_job()
