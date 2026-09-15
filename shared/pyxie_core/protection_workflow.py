"""protection.backup_membership: add/remove a VM from a PBS-backed vzdump
backup job, or switch it to PVE's native all-guests mode. Scoped
specifically to PBS-targeted jobs (any vzdump job whose `storage` field
resolves to a Storage row of type='pbs') -- built after a real gap was
found live: a newly-created VM (including the one running PyXie itself)
can be silently absent from a backup job's explicit vmid list, because
that job never joins an explicit list on its own when it isn't already in
all-guests mode.

Synchronous write (PUT /cluster/backup/{id} returns no PVE task/UPID,
unlike migrate/shutdown/start) -- same shape as workload.resize's
set_vm_config call. Stage order: dry_run -> awaiting_approval ->
revalidating -> executing -> verifying -> audit. No monitoring stage:
there is nothing to poll.

Per-VM toggling matches how a human actually thinks about this, not just
"replace the whole list": when the job is already in all-guests mode,
unchecking one VM adds it to PVE's own `exclude` list rather than
materializing a giant explicit vmid list -- every OTHER VM, including ones
created later, stays auto-included. Only an explicit-list job (all=0)
edits the vmid list directly. See PveMaintenanceClient.update_backup_job.
"""

from sqlalchemy.orm import Session

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Operation, PveTarget, Storage, Workload
from .operations_engine import (
    OperationError,
    approve_operation,
    block_operation,
    create_operation,
    enter_stage,
    fail_operation,
)
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback


class ProtectionWorkflowError(Exception):
    pass


def _pbs_storage_names(db: Session, cluster_id) -> set[str]:
    return {s.name for s in db.query(Storage).filter(Storage.cluster_id == cluster_id, Storage.type == "pbs").all()}


def _parse_vmid_csv(value) -> list[int]:
    return sorted({int(v) for v in str(value or "").split(",") if v.strip()})


def _load_context(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return target


def _all_guests_baseline(job: dict, workloads_by_vmid: dict) -> set[int]:
    """The set of VMIDs actually covered by all=1 on this job. PVE's own
    `node` field on a backup job means "only run if executed on this
    node" -- vzdump backs up whichever guests are LOCAL to whichever node
    actually runs it, so an all=1 job pinned to one node only covers that
    node's guests, never the whole cluster. Previously this codebase
    treated all=1 as "every known workload, unconditionally," which is
    wrong for a node-scoped job -- reproduced live: a node-scoped all=1
    job incorrectly reported an off-node VM as included. `pool` scoping
    is NOT handled here -- PyXie doesn't track PVE resource pools at all yet, so a
    pool-scoped job is refused upstream (see dry_run_backup_membership)
    rather than this function silently guessing at its membership."""
    scope_node = job.get("node") or None
    if scope_node is None:
        return set(workloads_by_vmid.keys())
    return {vmid for vmid, wl in workloads_by_vmid.items() if wl.node and wl.node.name == scope_node}


def list_pbs_backup_jobs(db: Session, cluster: Cluster) -> list[dict]:
    """Read-only: every vzdump job on this cluster whose storage target is
    PBS-backed, with membership cross-referenced against PyXie's own known
    workloads so the UI can show real VM names, not just bare VMIDs."""
    target = _load_context(db, cluster)
    pbs_storages = _pbs_storage_names(db, cluster.id)
    read_client, _cred = build_pve_client(db, target)
    with read_client:
        jobs = read_client.backup_jobs()
    workloads_by_vmid = {
        w.vmid: w
        for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all()
    }

    out = []
    for job in jobs:
        if job.get("storage") not in pbs_storages:
            continue
        all_guests = bool(job.get("all"))
        exclude = set(_parse_vmid_csv(job.get("exclude"))) if all_guests else set()
        explicit_vmids = set(_parse_vmid_csv(job.get("vmid"))) if not all_guests else set()
        scope_node = job.get("node") or None
        scope_pool = job.get("pool") or None
        # PyXie doesn't track PVE resource pools yet -- a pool-scoped job's
        # real membership can't be computed here, so it's surfaced as
        # unsupported rather than silently guessing (finding #11).
        scope_supported = scope_pool is None
        baseline = _all_guests_baseline(job, workloads_by_vmid) if (all_guests and scope_supported) else set()

        members = []
        for vmid, wl in sorted(workloads_by_vmid.items()):
            if not scope_supported:
                included = False
            elif all_guests:
                included = vmid in baseline and vmid not in exclude
            else:
                included = vmid in explicit_vmids
            members.append({"workload_id": str(wl.id), "vmid": vmid, "name": wl.name, "included": included})

        out.append(
            {
                "job_id": job["id"],
                "storage": job["storage"],
                "schedule": job.get("schedule"),
                "enabled": bool(job.get("enabled", 1)),
                "all_guests": all_guests,
                "exclude_vmids": sorted(exclude),
                "vmid_list": sorted(explicit_vmids),
                "members": members,
                "unknown_vmids": sorted(explicit_vmids - set(workloads_by_vmid.keys())) if not all_guests else [],
                "scope_node": scope_node,
                "scope_pool": scope_pool,
                "scope_supported": scope_supported,
            }
        )
    return out


def dry_run_backup_membership(
    db: Session,
    cluster: Cluster,
    job_id: str,
    *,
    select_all: bool = False,
    add_vmids: list[int] | None = None,
    remove_vmids: list[int] | None = None,
    actor: str,
) -> Operation:
    """Exactly one of `select_all` or add/remove_vmids is expected per
    call. add/remove are diffs against the job's CURRENT live state, not a
    wholesale replace -- see module docstring for the exclude-vs-vmid-list
    branching."""
    target = _load_context(db, cluster)
    pbs_storages = _pbs_storage_names(db, cluster.id)
    read_client, _cred = build_pve_client(db, target)
    with read_client:
        jobs = read_client.backup_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if job is None:
        raise ProtectionWorkflowError(f"backup job {job_id!r} not found on this cluster")
    if job.get("storage") not in pbs_storages:
        raise ProtectionWorkflowError(f"backup job {job_id!r} does not target a PBS-backed storage -- out of scope for this action")
    if job.get("pool"):
        raise ProtectionWorkflowError(
            f"backup job {job_id!r} is scoped to PVE pool {job['pool']!r} -- PyXie doesn't track pool "
            f"membership yet, so it can't safely compute who this job actually covers. Not supported by this action."
        )

    # is_missing=True rows (a VM/CT PyXie once knew about but that no
    # longer exists on the cluster) must be excluded here, same as
    # list_pbs_backup_jobs already does -- otherwise "Select All" computes
    # its "everyone" baseline against PyXie's full historical inventory
    # instead of what's actually on the cluster right now, and proposes
    # adding guests that were deleted long ago -- a real gap found live:
    # two already-deleted VMs were offered by Select All after removal
    # from the cluster.
    workloads_by_vmid = {
        w.vmid: w
        for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all()
    }

    current_all = bool(job.get("all"))
    current_exclude = _parse_vmid_csv(job.get("exclude")) if current_all else []
    current_vmids = _parse_vmid_csv(job.get("vmid")) if not current_all else []

    if select_all:
        desired_all, desired_exclude, desired_vmids = True, [], []
    elif current_all:
        exclude_set = set(current_exclude)
        exclude_set |= set(remove_vmids or [])
        exclude_set -= set(add_vmids or [])
        desired_all, desired_exclude, desired_vmids = True, sorted(exclude_set), []
    else:
        vmid_set = set(current_vmids)
        vmid_set |= set(add_vmids or [])
        vmid_set -= set(remove_vmids or [])
        desired_all, desired_exclude, desired_vmids = False, [], sorted(vmid_set)

    context = {
        "job_id": job_id, "storage": job["storage"],
        "current_all": current_all, "current_exclude": current_exclude, "current_vmids": current_vmids,
        "desired_all": desired_all, "desired_exclude": desired_exclude, "desired_vmids": desired_vmids,
    }
    op = create_operation(db, "protection.backup_membership", cluster_id=cluster.id, context=context, created_by=actor)

    reasons: list[str] = []
    blocking_rules: list[str] = []
    if not desired_all and not desired_vmids:
        reasons.append("this would leave the job with no guests selected and not in all-guests mode -- nothing would be backed up")
        blocking_rules.append("SAFE-PROTECTION-001")

    def _named(vmids):
        return [{"vmid": v, "name": workloads_by_vmid[v].name if v in workloads_by_vmid else None} for v in vmids]

    all_guests_baseline = _all_guests_baseline(job, workloads_by_vmid)
    if desired_all and current_all:
        newly_added = sorted(set(current_exclude) - set(desired_exclude))
        newly_removed = sorted(set(desired_exclude) - set(current_exclude))
    elif desired_all and not current_all:
        newly_added = sorted(all_guests_baseline - set(current_vmids))
        newly_removed = []
    elif not desired_all and current_all:
        newly_added = []
        newly_removed = sorted(all_guests_baseline & set(current_exclude))
    else:
        newly_added = sorted(set(desired_vmids) - set(current_vmids))
        newly_removed = sorted(set(current_vmids) - set(desired_vmids))

    dry_run_result = {
        "job_id": job_id, "storage": job["storage"],
        "current_all": current_all, "desired_all": desired_all,
        "desired_vmids": desired_vmids, "desired_exclude": desired_exclude,
        "added": _named(newly_added), "removed": _named(newly_removed),
        "eligible": len(blocking_rules) == 0, "reasons": reasons, "blocking_safety_rules": blocking_rules,
    }

    if blocking_rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_backup_membership(db: Session, operation_id) -> Operation:
    """Resumable, same convention as every other workflow module here."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise ProtectionWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _load_context(db, cluster)
    ctx = op.context or {}
    job_id = ctx["job_id"]
    desired_all = ctx["desired_all"]
    desired_exclude = ctx["desired_exclude"]
    desired_vmids = ctx["desired_vmids"]

    try:
        # "revalidating" is included here (not just "approved") because
        # enter_stage() below persists that status BEFORE this block
        # finishes -- a worker crash/restart between that write and
        # reaching "executing" must resume back into this same block, not
        # fall through to "unexpected state". acquire_lock() is already
        # idempotent for a resumed same-operation_id call, so re-running the whole block is
        # safe, not just re-entrant by accident.
        if op.status in ("approved", "revalidating"):
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                jobs = read_client.backup_jobs()
            job = next((j for j in jobs if j["id"] == job_id), None)
            if job is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"backup job {job_id!r} no longer exists")
            live_all = bool(job.get("all"))
            live_exclude = _parse_vmid_csv(job.get("exclude")) if live_all else []
            live_vmids = _parse_vmid_csv(job.get("vmid")) if not live_all else []
            # Someone editing this same job directly in the PVE UI between
            # dry-run and approval is drift a human should see, not paper
            # over by blindly overwriting whatever they just set.
            if live_all != ctx["current_all"] or live_exclude != ctx["current_exclude"] or live_vmids != ctx["current_vmids"]:
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-PROTECTION-002"], actor="system")

            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason="protection.backup_membership")
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing":
            maintenance_creds = load_pve_credentials(db, target, "maintenance")
            with PveMaintenanceClient(maintenance_creds) as write_client:
                write_client.update_backup_job(
                    job_id,
                    all_guests=desired_all,
                    vmid=",".join(str(v) for v in desired_vmids) if not desired_all else None,
                    exclude=",".join(str(v) for v in desired_exclude) if desired_all and desired_exclude else None,
                )
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                jobs = read_client.backup_jobs()
            job = next((j for j in jobs if j["id"] == job_id), None)
            live_all = bool(job.get("all")) if job else None
            live_exclude = _parse_vmid_csv(job.get("exclude")) if job and live_all else []
            live_vmids = _parse_vmid_csv(job.get("vmid")) if job and not live_all else []
            op.verification_result = {"all": live_all, "exclude": live_exclude, "vmid": live_vmids}
            db.commit()

            mismatch = (
                job is None
                or live_all != desired_all
                or (desired_all and live_exclude != desired_exclude)
                or (not desired_all and live_vmids != desired_vmids)
            )
            if mismatch:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"read-back after update didn't match: all={live_all}, exclude={live_exclude}, vmid={live_vmids}")

            release_locks_for_operation(db, op.id)
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except MutationsDisabledError as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
