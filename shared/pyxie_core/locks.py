"""Scoped execution locks for the operations engine.

These are logical locks inside PyXie's own database -- they stop two PyXie
operations from fighting each other over the same node/workload/cluster.
They cannot and do not prevent something else (a human on the PVE console,
another tool) from changing the resource directly; every operation still
re-reads live PVE state immediately before acting (see preconditions.py).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Operation, ResourceLock

DEFAULT_TTL_SECONDS = 3600


class LockContention(Exception):
    """Raised when a resource is already locked by a different in-flight operation."""

    def __init__(self, resource_type: str, resource_id: uuid.UUID, held_by_operation_id: uuid.UUID):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.held_by_operation_id = held_by_operation_id
        super().__init__(
            f"{resource_type} {resource_id} is locked by operation {held_by_operation_id}"
        )


def _active_lock(db: Session, resource_type: str, resource_id: uuid.UUID) -> Optional[ResourceLock]:
    now = datetime.now(timezone.utc)
    return (
        db.query(ResourceLock)
        .filter(
            ResourceLock.resource_type == resource_type,
            ResourceLock.resource_id == resource_id,
            ResourceLock.released_at.is_(None),
            ResourceLock.expires_at > now,
        )
        .one_or_none()
    )


def _is_ancestor_operation(db: Session, candidate_ancestor_id: uuid.UUID, operation_id: uuid.UUID, *, max_depth: int = 10) -> bool:
    """True if candidate_ancestor_id IS operation_id, or is an ancestor of it
    via parent_operation_id (e.g. the node.evacuate/maintenance.run that
    spawned this vm.live_migrate as a child). Walk is depth-bounded so a
    data anomaly can't spin forever."""
    current_id = operation_id
    for _ in range(max_depth):
        if current_id == candidate_ancestor_id:
            return True
        current_id = db.query(Operation.parent_operation_id).filter(Operation.id == current_id).scalar()
        if current_id is None:
            return False
    return False


def _release_expired(db: Session, resource_type: str, resource_id: uuid.UUID) -> None:
    """Marks any lock on this resource that has expired but was never
    explicitly released (a crashed/killed worker never got to call
    release_locks_for_operation) as released, right now.

    This makes released_at IS NULL an honest, race-safe signal for the
    partial unique index (see migration 0024) to enforce -- without this,
    an expired-but-never-released row would permanently block any future
    lock on the same resource once that constraint exists, since the
    constraint has no notion of expiry, only of released_at."""
    now = datetime.now(timezone.utc)
    db.query(ResourceLock).filter(
        ResourceLock.resource_type == resource_type,
        ResourceLock.resource_id == resource_id,
        ResourceLock.released_at.is_(None),
        ResourceLock.expires_at <= now,
    ).update({"released_at": now})
    db.commit()


def acquire_lock(
    db: Session,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    operation_id: uuid.UUID,
    reason: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> ResourceLock:
    """Acquire a lock, or raise LockContention if another live operation holds it.

    A lock already held by the SAME operation_id is idempotent (re-acquiring
    on resume after a worker restart is expected and must not fail). A lock
    already held by an ANCESTOR operation is also not contention -- e.g. a
    parent node.evacuate holds a node-level lock on the node being
    evacuated for its whole run, while each child vm.live_migrate it spawns
    independently tries to lock that same node as part of its own Safety
    Contract; that's the same logical actor, not two operations fighting
    over the resource, so it must not raise (proven live: this exact case
    made every node.evacuate's first child migration fail with
    SAFE-LOCK-001 before this check existed).

    The SELECT-then-INSERT below is not atomic on its own -- two workers
    racing between them could both see "no active lock" and both insert a
    row for the same resource. Migration 0024 adds a partial unique index
    on (resource_type, resource_id) WHERE released_at IS NULL so Postgres
    itself refuses the loser's insert; the IntegrityError it raises is
    caught below and translated into the same LockContention a caller
    already knows how to handle -- a single-worker deployment only
    reduces the probability of a race here, it does not make the
    SELECT-then-INSERT pattern correct on its own.
    """
    _release_expired(db, resource_type, resource_id)

    existing = _active_lock(db, resource_type, resource_id)
    if existing is not None:
        if existing.operation_id == operation_id:
            return existing
        if _is_ancestor_operation(db, existing.operation_id, operation_id):
            return existing
        raise LockContention(resource_type, resource_id, existing.operation_id)

    lock = ResourceLock(
        resource_type=resource_type,
        resource_id=resource_id,
        operation_id=operation_id,
        reason=reason,
        acquired_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )
    db.add(lock)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _active_lock(db, resource_type, resource_id)
        if existing is not None:
            if existing.operation_id == operation_id or _is_ancestor_operation(db, existing.operation_id, operation_id):
                return existing
            raise LockContention(resource_type, resource_id, existing.operation_id) from None
        # Lost the race, but the winner already released by the time we
        # looked -- safe to retry once rather than fail a legitimate
        # acquire over a transient collision.
        return acquire_lock(db, resource_type=resource_type, resource_id=resource_id, operation_id=operation_id, reason=reason, ttl_seconds=ttl_seconds)
    db.refresh(lock)
    return lock


def release_locks_for_operation(db: Session, operation_id: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    locks = (
        db.query(ResourceLock)
        .filter(ResourceLock.operation_id == operation_id, ResourceLock.released_at.is_(None))
        .all()
    )
    for lock in locks:
        lock.released_at = now
    db.commit()
    return len(locks)


def is_locked(db: Session, resource_type: str, resource_id: uuid.UUID) -> bool:
    return _active_lock(db, resource_type, resource_id) is not None
