"""The generic, resumable operation state machine.

An Operation row IS the state -- current stage, completed/pending stages,
dry-run/precondition/verification results all live in the row itself, not
in a worker's local memory. A workflow module (e.g. migration_workflow.py)
supplies the actual per-stage logic for one operation_type; this module
only knows how to create operations, move them between stages, and find
operations that need to be resumed after a restart.

Never infer success from an API response. Each workflow module is
responsible for actually verifying PVE-side state before calling
complete_operation() -- this engine does not and cannot know whether a
particular operation_type's mutation actually took effect.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from .audit import write_audit_event
from .locks import release_locks_for_operation
from .models import Operation, OperationType

TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled"}


class OperationError(Exception):
    pass


class OperationTypeDisabled(OperationError):
    pass


def now():
    return datetime.now(timezone.utc)


def create_operation(
    db: Session,
    operation_type_id: str,
    *,
    cluster_id=None,
    node_id=None,
    workload_id=None,
    plan_id=None,
    parent_operation_id=None,
    correlation_id: Optional[uuid.UUID] = None,
    context: Optional[dict[str, Any]] = None,
    created_by: str,
) -> Operation:
    op_type = db.query(OperationType).filter(OperationType.id == operation_type_id).one_or_none()
    if op_type is None:
        raise OperationError(f"unknown operation_type_id {operation_type_id!r}")
    if not op_type.enabled:
        raise OperationTypeDisabled(
            f"operation type {operation_type_id!r} is seeded but not yet enabled for execution"
        )

    stages = list(op_type.stages)
    op = Operation(
        operation_type_id=operation_type_id,
        correlation_id=correlation_id or uuid.uuid4(),
        plan_id=plan_id,
        parent_operation_id=parent_operation_id,
        cluster_id=cluster_id,
        node_id=node_id,
        workload_id=workload_id,
        status="pending",
        stage=stages[0] if stages else None,
        stages_completed=[],
        stages_pending=stages,
        context=context or {},
        rollback_classification=op_type.default_rollback_classification,
        created_by=created_by,
    )
    db.add(op)
    db.commit()
    db.refresh(op)

    write_audit_event(
        db,
        event_category="operation",
        event_type=f"{operation_type_id}.created",
        actor=created_by,
        actor_type="user",
        cluster_id=cluster_id,
        node_id=node_id,
        workload_id=workload_id,
        operation=operation_type_id,
        correlation_id=op.correlation_id,
        plan_id=plan_id,
        state_after={"context": context or {}},
        rollback_classification=op_type.default_rollback_classification,
        metadata={"operation_id": str(op.id)},
    )
    return op


def enter_stage(
    db: Session,
    op: Operation,
    *,
    status: str,
    stage: Optional[str] = None,
    actor: str = "system",
    **extra_fields: Any,
) -> Operation:
    """Move an operation into a new status/stage, persist any extra result
    fields on it, and write one audit event. This is the ONLY place that
    should mutate op.status/op.stage so the audit trail and the row always
    agree with each other.
    """
    previous_status = op.status
    previous_stage = op.stage

    if stage and stage in op.stages_pending:
        pending = list(op.stages_pending)
        pending.remove(stage)
        completed = list(op.stages_completed)
        if previous_stage and previous_stage not in completed and previous_stage != stage:
            completed.append(previous_stage)
        op.stages_pending = pending
        op.stages_completed = completed

    op.status = status
    if stage:
        op.stage = stage
    for key, value in extra_fields.items():
        setattr(op, key, value)

    if status == "executing" and op.started_at is None:
        op.started_at = now()
    if status in TERMINAL_STATUSES:
        op.completed_at = now()

    db.add(op)
    db.commit()
    db.refresh(op)

    write_audit_event(
        db,
        event_category="operation",
        event_type=f"{op.operation_type_id}.{status}",
        actor=actor,
        actor_type="system" if actor == "system" else "user",
        cluster_id=op.cluster_id,
        node_id=op.node_id,
        workload_id=op.workload_id,
        operation=op.operation_type_id,
        correlation_id=op.correlation_id,
        plan_id=op.plan_id,
        state_before={"status": previous_status, "stage": previous_stage},
        state_after={"status": status, "stage": op.stage},
        result="failure" if status in ("failed", "blocked") else "success",
        severity="error" if status == "failed" else ("warning" if status == "blocked" else "info"),
        error=op.error if status == "failed" else None,
        rollback_classification=op.rollback_classification,
        metadata={"operation_id": str(op.id)},
    )
    return op


def approve_operation(db: Session, op: Operation, *, approved_by: str) -> Operation:
    if op.status != "awaiting_approval":
        raise OperationError(f"operation {op.id} is not awaiting approval (status={op.status})")
    op.approved_by = approved_by
    op.approved_at = now()
    return enter_stage(db, op, status="approved", actor=approved_by)


def fail_operation(db: Session, op: Operation, *, error: str, actor: str = "system") -> Operation:
    return enter_stage(db, op, status="failed", error=error, actor=actor)


def block_operation(db: Session, op: Operation, *, blocking_safety_rules: list[str], actor: str = "system") -> Operation:
    return enter_stage(db, op, status="blocked", blocking_safety_rules=blocking_safety_rules, actor=actor)


def cancel_operation(db: Session, op: Operation, *, actor: str = "system") -> Operation:
    return enter_stage(db, op, status="cancelled", error="Cancelled by user request.", actor=actor)


def check_cancel_requested(db: Session, op: Operation) -> Operation | None:
    """Cooperative cancellation checkpoint for a resumable multi-step
    operation (node.enter_maintenance/maintenance.run/node.evacuate).
    Call this BETWEEN plan items -- never mid-transfer, since a live
    migration or patch apply already underway should finish rather than
    be torn out from under PVE. A cancel request is written by a
    different request (POST .../cancel, see operations.py) than whichever
    process is running this loop, so op.context is re-read fresh from the
    DB rather than trusting the in-memory copy passed in.

    Returns the now-cancelled Operation if a cancellation was pending (the
    caller should `return` it immediately), or None to keep going -- backs
    the Cancel button in Tasks, for when a problem is spotted partway
    through a long run."""
    db.refresh(op, attribute_names=["context"])
    if not (op.context or {}).get("cancel_requested"):
        return None
    release_locks_for_operation(db, op.id)
    return cancel_operation(db, op)


def get_resumable_operations(db: Session) -> list[Operation]:
    """Operations that are mid-flight and need a worker to pick them back up
    -- i.e. everything not in a terminal status and not sitting in
    awaiting_approval (which is waiting on a human, not a worker)."""
    return (
        db.query(Operation)
        .filter(
            Operation.status.notin_(TERMINAL_STATUSES | {"awaiting_approval", "pending", "dry_run"})
        )
        .all()
    )
