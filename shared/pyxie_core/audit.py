import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditEvent
from .request_context import current_client_ip


def write_audit_event(
    db: Session,
    *,
    event_category: str,
    event_type: str,
    actor: Optional[str] = None,
    actor_type: str = "system",
    organization_id=None,
    site_id=None,
    cluster_id=None,
    node_id=None,
    workload_id=None,
    provider_id=None,
    operation: Optional[str] = None,
    correlation_id: Optional[uuid.UUID] = None,
    plan_id: Optional[uuid.UUID] = None,
    state_before: Optional[dict[str, Any]] = None,
    state_after: Optional[dict[str, Any]] = None,
    result: str = "success",
    severity: str = "info",
    error: Optional[str] = None,
    rollback_classification: Optional[str] = None,
    justification: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> AuditEvent:
    """Write one audit event using the full future-proof envelope.

    Never pass secret values in state_before/state_after/metadata -- callers
    are responsible for redacting credential material before calling this.
    """
    # Record where the request came from (set per API request; absent for
    # worker/system events). Never overrides a caller-supplied value.
    meta = dict(metadata) if metadata else {}
    client_ip = current_client_ip.get()
    if client_ip and "client_ip" not in meta:
        meta["client_ip"] = client_ip

    event = AuditEvent(
        event_category=event_category,
        event_type=event_type,
        actor=actor,
        actor_type=actor_type,
        organization_id=organization_id,
        site_id=site_id,
        cluster_id=cluster_id,
        node_id=node_id,
        workload_id=workload_id,
        provider_id=provider_id,
        operation=operation,
        correlation_id=correlation_id,
        plan_id=plan_id,
        state_before=state_before,
        state_after=state_after,
        result=result,
        severity=severity,
        error=error,
        rollback_classification=rollback_classification,
        justification=justification,
        event_metadata=meta or None,
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    return event
