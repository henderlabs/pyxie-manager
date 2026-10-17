"""protection.backup_retention: change how many backups a Proxmox backup job keeps (its `prune-backups` setting).

Same shape as protection.backup_membership: a synchronous PUT on /cluster/backup/{id}, so there is no task to wait on.
Preview -> approve -> revalidate (the job must still look as previewed) -> lock -> write -> read back -> audit.
An empty selection removes the setting, and the job then keeps everything it makes unless the storage or the backup
server prunes. For a job that writes to a Proxmox Backup Server the backup server's prune jobs usually decide instead.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .audit import write_audit_event
from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Operation, PveTarget
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback

KEEP = ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")


def parse_prune(value) -> dict:
    """'keep-daily=7,keep-weekly=4' (or PVE's already-split dict) -> {'keep-daily': 7, 'keep-weekly': 4}."""
    if isinstance(value, dict):
        raw = value
    else:
        raw = dict(p.split("=", 1) for p in str(value or "").split(",") if "=" in p)
    out = {}
    for k in KEEP:
        try:
            if raw.get(k) not in (None, "", 0, "0"):
                out[k] = int(raw[k])
        except (TypeError, ValueError):
            continue
    return out


def format_prune(keep: dict) -> str:
    return ",".join(f"{k}={keep[k]}" for k in KEEP if keep.get(k))


def describe(keep: dict) -> str:
    return "keep " + ", ".join(f"{k[5:]} {v}" for k, v in ((k, keep[k]) for k in KEEP if keep.get(k))) if keep else "no limit set on this job"


def _job(db: Session, cluster: Cluster, job_id: str):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    client, _c = build_pve_client(db, target)
    with client:
        jobs = client.backup_jobs()
    return target, next((j for j in jobs if j["id"] == job_id), None)


def dry_run_job_retention(db: Session, cluster: Cluster, job_id: str, keep_in: dict, *, actor: str) -> Operation:
    reasons: list[str] = []
    rules: list[str] = []
    keep: dict = {}
    for k, v in (keep_in or {}).items():
        if k not in KEEP:
            reasons.append(f"unknown setting {k}")
            rules.append("SAFE-PROTECTION-001")
            continue
        if v in (None, ""):
            continue
        try:
            n = int(v)
            if n < 1 or n > 9999:
                raise ValueError
            keep[k] = n
        except (TypeError, ValueError):
            reasons.append(f"{k} must be a whole number from 1 to 9999")
            rules.append("SAFE-PROTECTION-001")
    target, job = _job(db, cluster, job_id)
    if job is None:
        raise OperationError(f"backup job {job_id!r} not found on this cluster")
    before = parse_prune(job.get("prune-backups"))
    ctx = {"job_id": job_id, "storage": job.get("storage"), "before": before, "desired": keep}
    op = create_operation(db, "protection.backup_retention", cluster_id=cluster.id, context=ctx, created_by=actor)
    if before == keep:
        reasons.append("nothing to change: the job already has this retention")
        rules.append("SAFE-PROTECTION-001")
    if not rules:
        reasons.append(f"Backup job {job_id} (to {job.get('storage')}): {describe(before)} -> {describe(keep)}.")
        if keep:
            reasons.append("After each run Proxmox deletes this job's older backups that fall outside the new limits. Deleted backups cannot be brought back.")
        else:
            reasons.append("The job stops pruning. Backups then pile up unless the storage or the backup server prunes them.")
    result = {"job_id": job_id, "storage": job.get("storage"), "before": before, "desired": keep, "eligible": not rules, "reasons": reasons, "blocking_safety_rules": sorted(set(rules))}
    if rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(rules)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_job_retention(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise OperationError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    job_id, desired = ctx["job_id"], ctx["desired"]
    try:
        if op.status in ("approved", "revalidating"):
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            target, job = _job(db, cluster, job_id)
            if job is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"backup job {job_id!r} no longer exists")
            if parse_prune(job.get("prune-backups")) != ctx["before"]:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-PROTECTION-002"], actor="system")
            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason="protection.backup_retention")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")
        else:
            target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
        if op.status == "executing":
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                wc.set_backup_job_retention(job_id, format_prune(desired) or None)
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            _t, job = _job(db, cluster, job_id)
            live = parse_prune(job.get("prune-backups")) if job else None
            op.verification_result = {"prune-backups": live}
            db.commit()
            release_locks_for_operation(db, op.id)
            if live != desired:
                return fail_operation(db, op, error=f"read-back after the change did not match: the job has {describe(live or {})}")
            write_audit_event(db, event_category="protection", event_type="backup_job.retention_changed", actor=op.approved_by or "system", actor_type="user",
                              metadata={"job": job_id, "before": ctx["before"], "after": desired})
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")


def advisories(policy_keep: dict, ds_name: str | None, ds: dict | None, prune_jobs: list[dict]) -> list[dict]:
    """Warnings for one policy about the datastore it writes to. `policy_keep`: {'keep-daily': 7}; `ds`: {'gc_schedule', 'backend'} or None when unknown;
    `prune_jobs`: that datastore's PBS prune jobs (settings in PBS form). A PBS prune job deletes whatever its own limits do not keep, so where it keeps fewer
    than the policy asks for, the policy's longer retention is cut short. Pure."""
    out: list[dict] = []
    if ds_name is None:
        return [{"level": "info", "text": "not a backup server datastore: PBS prune and garbage collection do not apply"}]
    if ds is None:
        return [{"level": "info", "text": f"{ds_name}: cannot read its prune and garbage-collection settings"}]
    if not ds.get("gc_schedule"):
        out.append({"level": "warn", "text": f"{ds_name} has no garbage-collection schedule: pruned backups keep using space (set one on the Protection overview)"})
    pruning = [j for j in prune_jobs if not j.get("disable")]
    if not policy_keep:
        out.append({"level": "warn", "text": "no limits set: the policy keeps every backup" + (f" unless {ds_name}'s prune job removes them" if pruning else "")})
    for j in pruning:
        short = [f"{k[5:]} {j.get(k) or 0} (policy {v})" for k, v in policy_keep.items() if int(j.get(k) or 0) < v]
        if short:
            out.append({"level": "warn", "text": f"prune job {j.get('id')} on {ds_name} keeps fewer: " + ", ".join(short) + ". It will delete backups sooner than this policy says"})
    if not pruning and policy_keep:
        out.append({"level": "info", "text": f"{ds_name} has no prune job: only this policy's own pruning applies"})
    return out
