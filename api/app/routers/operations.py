import copy
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, object_session

from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Node, Operation, PveTarget, Storage, User, Workload
from pyxie_core.migration_workflow import MigrationWorkflowError, approve as approve_migration, dry_run_migration
from pyxie_core.audit import write_audit_event
from pyxie_core.locks import release_locks_for_operation
from pyxie_core.operations_engine import OperationError, TERMINAL_STATUSES, cancel_operation
from pyxie_core import placement
from pyxie_core.placement import get_cluster_storage_preference, is_currently_on_shared_storage, recommend_destinations, recommend_storage_for_candidate

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/operations", tags=["operations"], dependencies=[Depends(get_current_user)])


def _resolve_actor(op: Operation, field: str):
    """Who actually did it. An operation spawned by a bigger one (each migration
    inside a bulk migrate, an evacuation, a rebalance) records the PARENT as its
    actor ("cluster.rebalance:<uuid>"), not the person; walk up to the operation
    a human started. `field` is "created_by" (who clicked Preview/initiate) or
    "approved_by" (who clicked Approve)."""
    sess = object_session(op)
    cur, value, hops = op, getattr(op, field), 0
    while sess is not None and value and "@" not in value and ":" in value and cur.parent_operation_id is not None and hops < 4:
        parent = sess.get(Operation, cur.parent_operation_id)
        if parent is None:
            break
        cur, value, hops = parent, getattr(parent, field), hops + 1
    return value


def _actor_label(op: Operation, value):
    """Display name for a person (their PyXie display name, else the part of the
    email before the @); \"PyXie (automatic)\" for anything not started by a
    person. Cached per request so a 100-row list doesn't do 100 user lookups."""
    if not value:
        return None
    if "@" not in value:
        return "PyXie (automatic)"
    sess = object_session(op)
    if sess is None:
        return value.split("@")[0]
    cache = sess.info.setdefault("actor_labels", {})
    if value not in cache:
        user = sess.query(User).filter(User.email == value).one_or_none()
        cache[value] = (user.display_name if user and user.display_name else value.split("@")[0])
    return cache[value]


def _with_storage_names(op: Operation) -> dict | None:
    """The operation's context with each plan line's destination storage spelled out (name and scope), so the plan can
    show WHERE a disk will land, not only which node. Read-only: a copy, nothing is written back."""
    ctx = op.context
    if not isinstance(ctx, dict):
        return ctx
    db = object_session(op)
    out = ctx
    for key in ("migrate_plan", "plan"):
        items = ctx.get(key)
        if db is None or not isinstance(items, list):
            continue
        ids = {i.get("destination_storage_id") for i in items if isinstance(i, dict) and i.get("destination_storage_id")}
        if not ids:
            continue
        names = {str(s.id): s for s in db.query(Storage).filter(Storage.id.in_(list(ids))).all()}
        enriched = []
        for i in items:
            st = names.get(str(i.get("destination_storage_id"))) if isinstance(i, dict) else None
            enriched.append({**i, "destination_storage": st.name, "destination_storage_scope": st.scope} if st else i)
        out = {**out, key: enriched}
    return out


def _serialize(op: Operation) -> dict:
    initiator = _resolve_actor(op, "created_by")
    approver = _resolve_actor(op, "approved_by")
    return {
        # Resolved to the person: for a migration run inside a bigger operation this is
        # whoever started that operation, not "cluster.rebalance:<id>".
        "initiated_by": _actor_label(op, initiator),
        "initiated_by_email": initiator if initiator and "@" in initiator else None,
        "approver": _actor_label(op, approver),
        "approver_email": approver if approver and "@" in approver else None,
        "id": str(op.id),
        "operation_type_id": op.operation_type_id,
        "correlation_id": str(op.correlation_id),
        "plan_id": str(op.plan_id) if op.plan_id else None,
        "parent_operation_id": str(op.parent_operation_id) if op.parent_operation_id else None,
        "cluster_id": str(op.cluster_id) if op.cluster_id else None,
        "node_id": str(op.node_id) if op.node_id else None,
        "workload_id": str(op.workload_id) if op.workload_id else None,
        "status": op.status,
        "stage": op.stage,
        "stages_completed": op.stages_completed,
        "stages_pending": op.stages_pending,
        "context": _with_storage_names(op),
        "dry_run_result": op.dry_run_result,
        "progress": op.progress,
        "precondition_snapshot": op.precondition_snapshot,
        "pve_upid": op.pve_upid,
        "pve_task_result": op.pve_task_result,
        "verification_result": op.verification_result,
        "rollback_classification": op.rollback_classification,
        "blocking_safety_rules": op.blocking_safety_rules,
        "error": op.error,
        "created_by": op.created_by,
        "dismissed": op.dismissed,
        "approved_by": op.approved_by,
        "approved_at": op.approved_at.isoformat() if op.approved_at else None,
        "created_at": op.created_at.isoformat(),
        "updated_at": op.updated_at.isoformat(),
        "started_at": op.started_at.isoformat() if op.started_at else None,
        "completed_at": op.completed_at.isoformat() if op.completed_at else None,
    }


def _existing_live_op(db: Session, operation_type_id: str, node_id) -> Operation | None:
    """A second dry-run request for the same node+action while an earlier
    one is still live (not yet terminal, not dismissed) returns that SAME
    operation instead of creating a duplicate sibling. Without this, two
    near-simultaneous clicks -- or a click plus an already-in-flight
    request from a different tab/session -- create two independent
    operations; if one gets approved and runs to completion, the other
    just sits in awaiting_approval forever with no way to reconcile it --
    a real gap found live: a maintenance-mode operation actually completed,
    but a leftover "awaiting approval" for the same node never cleared.
    Every node-scoped dry-run endpoint below shares this guard.
    """
    return (
        db.query(Operation)
        .filter(
            Operation.operation_type_id == operation_type_id,
            Operation.node_id == node_id,
            Operation.status.notin_(TERMINAL_STATUSES),
            Operation.dismissed.is_(False),
        )
        .order_by(Operation.created_at.desc())
        .first()
    )


@router.get("")
def list_operations(db: Session = Depends(get_db), limit: int = 100):
    rows = db.query(Operation).order_by(Operation.created_at.desc()).limit(min(limit, 500)).all()
    return [_serialize(o) for o in rows]


class DismissRequest(BaseModel):
    dismissed: bool = True


@router.post("/{operation_id}/dismiss", dependencies=[Depends(require_admin)])
def dismiss_operation(
    operation_id: uuid.UUID, payload: DismissRequest = DismissRequest(),
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Hides an operation from its default list view (Pending Actions for
    awaiting_approval, Migration History for terminal ones). Never deletes
    the row -- audit history, locks, and everything else about the
    operation stays exactly as it was; this only changes whether it
    clutters the everyday view. Toggleable, not one-way. Refuses only while
    something is actively in flight (approved/revalidating/executing/
    monitoring/verifying) -- that still needs to stay visible."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    dismissable = TERMINAL_STATUSES | {"awaiting_approval"}
    if payload.dismissed and op.status not in dismissable:
        raise HTTPException(409, f"operation is actively in flight (status={op.status}) -- cannot dismiss until it settles")
    op.dismissed = payload.dismissed
    db.commit()
    write_audit_event(
        db, event_category="operation", event_type=f"{op.operation_type_id}.dismissed" if payload.dismissed else f"{op.operation_type_id}.undismissed",
        actor=user.email, actor_type="user",
        metadata={"operation_id": str(op.id)},
    )
    return _serialize(op)


@router.get("/{operation_id}/log")
def get_operation_log(operation_id: uuid.UUID, after: int = 0, db: Session = Depends(get_db)):
    """Live output for an operation (host apt output and workflow stage lines), oldest first.
    The page passes the last `next` it saw as `after` to fetch only new chunks."""
    from pyxie_core.models import OperationLog

    rows = (
        db.query(OperationLog)
        .filter(OperationLog.operation_id == operation_id, OperationLog.id > max(after, 0))
        .order_by(OperationLog.id)
        .limit(500)
        .all()
    )
    return {
        "lines": [{"seq": r.id, "ts": r.ts.isoformat(), "source": r.source, "text": r.text} for r in rows],
        "next": rows[-1].id if rows else max(after, 0),
    }


@router.get("/{operation_id}")
def get_operation(operation_id: uuid.UUID, db: Session = Depends(get_db)):
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    return _serialize(op)


class MigrationDryRunRequest(BaseModel):
    workload_id: uuid.UUID
    destination_node_id: uuid.UUID
    destination_storage_id: uuid.UUID | None = None
    transport: str = "live"


@router.post("/vm-migrations/dry-run", dependencies=[Depends(require_admin)])
def create_migration_dry_run(payload: MigrationDryRunRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    destination_node = db.query(Node).filter(Node.id == payload.destination_node_id).one_or_none()
    if destination_node is None:
        raise HTTPException(404, "destination node not found")
    destination_storage = None
    if payload.destination_storage_id is not None:
        destination_storage = db.query(Storage).filter(Storage.id == payload.destination_storage_id).one_or_none()
        if destination_storage is None:
            raise HTTPException(404, "destination storage not found")
    try:
        op = dry_run_migration(
            db, workload, destination_node, actor=user.email, destination_storage=destination_storage,
            transport=payload.transport,
        )
    except MigrationWorkflowError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class RecommendRequest(BaseModel):
    workload_id: uuid.UUID


@router.post("/vm-migrations/recommend", dependencies=[Depends(require_admin)])
def recommend_migration_destination(payload: RecommendRequest, db: Session = Depends(get_db)):
    """Read-only DRS-style ranking of every online cluster node as a
    migration destination for this workload -- balance, performance/trust
    tiers, and PyXie affinity rules layered on top of the existing
    eligibility gates. Creates no operation; the migrate form uses this to
    pre-fill (not lock) the node/storage pickers."""
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")

    source_node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    candidates = (
        db.query(Node)
        .filter(Node.cluster_id == cluster.id, Node.id != source_node.id, Node.is_missing.is_(False), Node.status == "online", Node.maintenance_mode.is_(False))
        .all()
    )

    client = None
    try:
        client, _cred = build_pve_client(db, target)
    except Exception:
        pass

    # Whether THIS workload is actually on shared storage right now matters
    # a lot here -- recommending "keep current" only makes sense if there
    # genuinely IS a current shared placement to keep. Read it once, live,
    # rather than assuming shared-if-available (that previously produced a
    # confusing "keep the current shared storage" recommendation for a
    # workload whose real disk was node-local the whole time).
    currently_on_shared = False
    ctx = client if client else _NullClientCtx()
    with ctx:
        currently_on_shared = is_currently_on_shared_storage(client, source_node, workload, db)
        ranked = recommend_destinations(db, client, workload, candidates)

    effective_pref = workload.storage_preference or get_cluster_storage_preference(db, cluster.id)
    out = []
    for c in ranked:
        best_storage = recommend_storage_for_candidate(db, c.node_id, currently_on_shared, storage_preference=effective_pref)
        out.append({
            "node_id": str(c.node_id), "node_name": c.node_name, "blocked": c.blocked,
            "blocking_reasons": c.blocking_reasons, "score": c.score, "reasons": c.reasons,
            "recommended_storage": best_storage,
        })
    return {"candidates": out, "top_pick": next((c for c in out if not c["blocked"]), None)}


class _NullClientCtx:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


_JOB_TIMEOUT_BY_TYPE = {
    # A storage-relocating migration is disk-transfer-bound, not RAM-bound
    # -- migration_workflow.py's own internal monitoring timeout now scales
    # up to MIGRATION_TIMEOUT_CAP_SECONDS (4h) for that case, learned from a
    # real 80GB relocation that legitimately took 942s against the old
    # (too-short) fixed timeout. This RQ job timeout must always stay
    # comfortably above that internal cap, or RQ kills the job outright
    # before the internal timeout can fire and be handled gracefully.
    "vm.live_migrate": 16200,  # 4.5h -- 30min margin over the 4h internal cap
    # node.evacuate/maintenance.run process multiple workloads sequentially
    # within ONE job, each potentially a storage-relocating migration --
    # these are widened proportionally, but a node with many large-disk
    # workloads to move could still exceed this; known scaling limit, not
    # fully solved here.
    "node.evacuate": 10800,  # 3h
    "workload.shutdown": 600,
    "workload.start": 300,
    "workload.force_stop": 300,
    "workload.reboot": 600,
    "workload.move_storage": 21600,  # 6h: one Proxmox disk move per disk, each with its own size-based limit
    "workload.snapshot_create": 2400,
    "workload.snapshot_delete": 2400,
    "workload.snapshot_rollback": 2400,
    "workload.tags_change": 120,  # synchronous config write
    "cluster.tag_colors": 120,  # synchronous datacenter option write
    "workload.disk_grow": 1200,
    "workload.nic_change": 300,
    "workload.disk_change": 1200,
    "backup.policy_change": 300,
    "workload.create": 1800,
    "storage.iso_download": 7800,
    "storage.iso_delete": 900,
    "storage.iso_enable": 120,
    "workload.delete": 2400,
    "workload.clone": 8400,
    "workload.cdrom_change": 300,
    "workload.ct_create": 1800,
    "workload.ct_resize": 300,
    "pool.create": 600,
    "pool.delete": 600,
    "pool.members": 600,
    "storage.create": 600,
    "storage.remove": 600,
    "workload.ct_nic_change": 300,
    "storage.ct_template_download": 3900,
    "storage.ct_template_delete": 900,
    "workload.hw_change": 3600,  # optional snapshot + shutdown + config + start
    "workload.resize": 600,  # shutdown + config PUT + start, generous margin over each's own wait_for_task timeout
    "host.reboot": 1800,
    "pbs.update": 3900,
    "pbs.reboot": 1800,
    "pbs.datastore_change": 300,
    "pbs.storage_add": 300,
    "workload.restore": 41000,  # up to 10 h for the restore task plus configuring and starting
    "pbs.maintenance": 7200,
    "pbs.job_change": 300,
    "host.update": 3900,  # comfortably above host_maintenance_client's 3600s apply() timeout
    "maintenance.run": 21600,  # 6h
    "node.enter_maintenance": 10800,  # 3h -- same evacuation shape as node.evacuate
    "node.exit_maintenance": 1800,
    "cluster.rebalance": 10800,  # 3h -- same evacuation shape as node.evacuate
    "protection.backup_membership": 120,  # synchronous PVE config write, no task to wait on
    "protection.backup_retention": 120,
    "workload.network_vlan_change": 120,  # synchronous PVE config write, no task to wait on -- same shape
}


def _approve_for_type(db: Session, op: Operation, actor: str) -> Operation:
    if op.operation_type_id == "vm.live_migrate":
        return approve_migration(db, op, approved_by=actor)
    if op.operation_type_id == "node.evacuate":
        from pyxie_core.evacuation_workflow import approve as approve_evac
        return approve_evac(db, op, approved_by=actor)
    if op.operation_type_id in ("workload.shutdown", "workload.start", "workload.force_stop", "workload.reboot"):
        from pyxie_core.lifecycle_workflow import approve as approve_lifecycle
        return approve_lifecycle(db, op, approved_by=actor)
    if op.operation_type_id == "workload.resize":
        from pyxie_core.resize_workflow import approve as approve_resize
        return approve_resize(db, op, approved_by=actor)
    if op.operation_type_id == "workload.move_storage":
        from pyxie_core.storage_move_workflow import approve as approve_storage_move
        return approve_storage_move(db, op, approved_by=actor)
    if op.operation_type_id in ("workload.snapshot_create", "workload.snapshot_delete", "workload.snapshot_rollback"):
        from pyxie_core.snapshot_workflow import approve as approve_snapshot
        return approve_snapshot(db, op, approved_by=actor)
    if op.operation_type_id == "workload.tags_change":
        from pyxie_core.tags_workflow import approve as approve_tags
        return approve_tags(db, op, approved_by=actor)
    if op.operation_type_id in ("pool.create", "pool.delete", "pool.members"):
        from pyxie_core.pool_workflow import approve as approve_pool
        return approve_pool(db, op, approved_by=actor)
    if op.operation_type_id in ("storage.create", "storage.remove"):
        from pyxie_core.storage_workflow import approve as approve_storage
        return approve_storage(db, op, approved_by=actor)
    if op.operation_type_id in ("workload.ct_resize", "workload.ct_nic_change"):
        from pyxie_core.ct_edit_workflow import approve as approve_ct_edit
        return approve_ct_edit(db, op, approved_by=actor)
    if op.operation_type_id in ("workload.ct_create", "storage.ct_template_download", "storage.ct_template_delete"):
        from pyxie_core.ct_workflow import approve as approve_ct
        return approve_ct(db, op, approved_by=actor)
    if op.operation_type_id == "workload.cdrom_change":
        from pyxie_core.cdrom_workflow import approve as approve_cdrom
        return approve_cdrom(db, op, approved_by=actor)
    if op.operation_type_id == "workload.clone":
        from pyxie_core.clone_workflow import approve as approve_clone
        return approve_clone(db, op, approved_by=actor)
    if op.operation_type_id == "workload.delete":
        from pyxie_core.delete_workflow import approve as approve_delete
        return approve_delete(db, op, approved_by=actor)
    if op.operation_type_id in ("storage.iso_download", "storage.iso_delete", "storage.iso_enable"):
        from pyxie_core.iso_workflow import approve as approve_iso
        return approve_iso(db, op, approved_by=actor)
    if op.operation_type_id == "workload.create":
        from pyxie_core.vm_create_workflow import approve as approve_create
        return approve_create(db, op, approved_by=actor)
    if op.operation_type_id == "backup.policy_change":
        from pyxie_core.backup_policy_workflow import approve as approve_backup_policy
        return approve_backup_policy(db, op, approved_by=actor)
    if op.operation_type_id == "workload.disk_change":
        from pyxie_core.disk_workflow import approve as approve_disk
        return approve_disk(db, op, approved_by=actor)
    if op.operation_type_id == "workload.nic_change":
        from pyxie_core.nic_workflow import approve as approve_nic
        return approve_nic(db, op, approved_by=actor)
    if op.operation_type_id in ("workload.disk_grow", "workload.hw_change"):
        from pyxie_core.hw_edit_workflow import approve as approve_hw_edit
        return approve_hw_edit(db, op, approved_by=actor)
    if op.operation_type_id == "cluster.tag_colors":
        from pyxie_core.tag_colors_workflow import approve as approve_tag_colors
        return approve_tag_colors(db, op, approved_by=actor)
    if op.operation_type_id == "host.reboot":
        from pyxie_core.reboot_workflow import approve as approve_reboot
        return approve_reboot(db, op, approved_by=actor)
    if op.operation_type_id == "pbs.storage_add":
        from pyxie_core.pbs_storage_workflow import approve as approve_pbs_store
        return approve_pbs_store(db, op, approved_by=actor)
    if op.operation_type_id == "pbs.datastore_change":
        from pyxie_core.pbs_datastore_workflow import approve as approve_pbs_ds
        return approve_pbs_ds(db, op, approved_by=actor)
    if op.operation_type_id == "workload.restore":
        from pyxie_core.restore_workflow import approve as approve_restore
        return approve_restore(db, op, approved_by=actor)
    if op.operation_type_id == "pbs.reboot":
        from pyxie_core.pbs_reboot_workflow import approve as approve_pbs_reboot
        return approve_pbs_reboot(db, op, approved_by=actor)
    if op.operation_type_id == "pbs.maintenance":
        from pyxie_core.pbs_maintenance_workflow import approve as approve_pbs_maint
        return approve_pbs_maint(db, op, approved_by=actor)
    if op.operation_type_id == "pbs.update":
        from pyxie_core.pbs_update_workflow import approve as approve_pbs_update
        return approve_pbs_update(db, op, approved_by=actor)
    if op.operation_type_id == "pbs.job_change":
        from pyxie_core.pbs_job_workflow import approve as approve_pbs_job
        return approve_pbs_job(db, op, approved_by=actor)
    if op.operation_type_id == "host.update":
        from pyxie_core.host_update_workflow import approve as approve_host_update
        return approve_host_update(db, op, approved_by=actor)
    if op.operation_type_id == "maintenance.run":
        from pyxie_core.maintenance_workflow import approve as approve_maintenance
        return approve_maintenance(db, op, approved_by=actor)
    if op.operation_type_id in ("node.enter_maintenance", "node.exit_maintenance"):
        from pyxie_core.node_maintenance_workflow import approve as approve_node_maintenance
        return approve_node_maintenance(db, op, approved_by=actor)
    if op.operation_type_id == "cluster.rebalance":
        from pyxie_core.balance_workflow import approve as approve_balance
        return approve_balance(db, op, approved_by=actor)
    if op.operation_type_id == "protection.backup_retention":
        from pyxie_core.retention_workflow import approve as approve_retention
        return approve_retention(db, op, approved_by=actor)
    if op.operation_type_id == "protection.backup_membership":
        from pyxie_core.protection_workflow import approve as approve_backup_membership
        return approve_backup_membership(db, op, approved_by=actor)
    if op.operation_type_id == "workload.network_vlan_change":
        from pyxie_core.network_workflow import approve as approve_vlan_change
        return approve_vlan_change(db, op, approved_by=actor)
    raise HTTPException(400, f"approval not implemented for operation type {op.operation_type_id!r} yet")


@router.post("/{operation_id}/approve", dependencies=[Depends(require_admin)])
def approve_operation_endpoint(operation_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    try:
        op = _approve_for_type(db, op, user.email)
    except OperationError as exc:
        raise HTTPException(409, str(exc))

    import os
    import redis
    from rq import Queue

    conn = redis.from_url(os.environ["REDIS_URL"])
    operations_q = Queue("operations", connection=conn)
    timeout = _JOB_TIMEOUT_BY_TYPE.get(op.operation_type_id, 1200)
    operations_q.enqueue("worker.jobs.execute_operation_job", str(op.id), job_timeout=timeout)

    return _serialize(op)


# Still awaiting a human, or hasn't started running anything yet --
# cancelling here is trivial and instant, no PVE state to unwind.
_PRE_EXECUTION_STATUSES = {"pending", "dry_run", "awaiting_approval"}

# The only types whose execute loop actually checks check_cancel_requested()
# between plan items (see node_maintenance_workflow.py/maintenance_workflow.py/
# evacuation_workflow.py) -- everything else (a single vm.live_migrate,
# host.update, host.reboot running on its own) has no safe checkpoint to
# stop at once PVE has already started moving bytes or installing packages,
# so cancel is only offered for these while they're pre-execution.
_COOPERATIVE_CANCEL_TYPES = {"node.enter_maintenance", "maintenance.run", "node.evacuate", "cluster.rebalance"}


@router.post("/{operation_id}/cancel", dependencies=[Depends(require_admin)])
def cancel_operation_endpoint(operation_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    if op.status in TERMINAL_STATUSES:
        raise HTTPException(409, f"already {op.status}, nothing to cancel")

    if op.status in _PRE_EXECUTION_STATUSES:
        release_locks_for_operation(db, op.id)
        op = cancel_operation(db, op, actor=user.email)
        return _serialize(op)

    if op.operation_type_id not in _COOPERATIVE_CANCEL_TYPES:
        raise HTTPException(
            400,
            f"{op.operation_type_id} is already executing and has no safe point to stop at mid-flight -- "
            "let it finish (or fail) on its own.",
        )

    # Cooperative: the running loop checks this itself between plan items
    # (never mid-migration/mid-patch) and cancels there -- see
    # check_cancel_requested() in operations_engine.py. Status is left
    # exactly as it is; the caller sees the request accepted, not that
    # cancellation already happened.
    ctx = dict(op.context or {})
    ctx["cancel_requested"] = True
    op.context = ctx
    db.commit()
    db.refresh(op)
    return _serialize(op)


# ---------------------------------------------------------------------------
# Interactive preview: node.enter_maintenance/maintenance.run/node.evacuate
# each carry a migrate_plan (or, for node.evacuate, "plan") of {workload,
# destination, ...} while sitting in awaiting_approval. Every item already
# stores the FULL ranked candidate list computed during planning (see
# recommend_destinations()/rank_with_simulated_load() in placement.py and
# their call sites), not just the one auto-picked -- this lets a reviewer
# override the destination for one workload before approving, instead of
# only ever getting the auto-chosen plan -- the preview should show
# everything it's going to do with options selectable right there, not
# just the auto-picked answer. Storage is recomputed from the same
# currently_on_shared/storage_preference this workload's OWN candidate
# search already used --
# purely from the DB, no fresh PVE call needed.
# ---------------------------------------------------------------------------

_PLAN_KEY_BY_TYPE = {
    "node.enter_maintenance": "migrate_plan",
    "maintenance.run": "migrate_plan",
    "node.evacuate": "plan",
    "cluster.rebalance": "migrate_plan",
}


class MigratePlanUpdateRequest(BaseModel):
    destination_node_id: uuid.UUID | None = None
    transport: str | None = None


@router.post("/{operation_id}/migrate-plan/{workload_id}", dependencies=[Depends(require_admin)])
def update_migrate_plan_destination(
    operation_id: uuid.UUID, workload_id: uuid.UUID, payload: MigratePlanUpdateRequest,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    if payload.destination_node_id is None and payload.transport is None:
        raise HTTPException(400, "nothing to update -- pass destination_node_id and/or transport")
    if payload.transport is not None and payload.transport not in ("live", "offline", "shutdown_in_place", "skip"):
        raise HTTPException(400, "transport must be 'live', 'offline', 'shutdown_in_place', or 'skip'")

    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    if payload.transport == "shutdown_in_place" and op.operation_type_id not in ("node.enter_maintenance", "maintenance.run"):
        # Only makes sense when the node stays in the cluster afterward --
        # node.evacuate/cluster.rebalance are about moving load OFF a node
        # for good, so there's nowhere to "restart it here" later.
        raise HTTPException(400, f"'shutdown_in_place' isn't available for {op.operation_type_id}")
    if payload.transport == "skip" and op.operation_type_id != "cluster.rebalance":
        # Balance Load / Bulk Migrate are optional moves, so any line can be dropped.
        # Maintenance and evacuation exist to empty the host: every VM has to go
        # somewhere or be shut down in place, so "don't move" would defeat them.
        raise HTTPException(400, f"'skip' isn't available for {op.operation_type_id}")
    plan_key = _PLAN_KEY_BY_TYPE.get(op.operation_type_id)
    if plan_key is None:
        raise HTTPException(400, f"{op.operation_type_id} has no editable migration plan")
    if op.status != "awaiting_approval":
        raise HTTPException(409, "the plan can only be edited while it's awaiting approval")

    ctx = dict(op.context or {})
    # Deep copy, not list(...) -- a shallow copy shares the SAME nested
    # item dicts still referenced by op.context's current (pre-flush)
    # value. Mutating `item` in place would then mutate that shared
    # object too, so SQLAlchemy's before/after history for the context
    # column would compare as equal (same content on both sides) and
    # silently skip the UPDATE entirely -- confirmed live 2026-09-13: the
    # endpoint returned 200 with the "updated" plan, but nothing was ever
    # persisted, because the "old" and "new" values were, by the time of
    # comparison, identical in content.
    plan = copy.deepcopy(ctx.get(plan_key) or [])
    item = next((p for p in plan if p.get("workload_id") == str(workload_id)), None)
    if item is None:
        raise HTTPException(404, "that workload isn't in this operation's plan")

    if payload.destination_node_id is not None:
        candidate = next((c for c in item.get("candidates", []) if c.get("node_id") == str(payload.destination_node_id)), None)
        if candidate is None:
            raise HTTPException(400, "that node wasn't offered as a candidate for this workload")
        # A "blocked" candidate is no longer refused outright -- it's still
        # shown with its reasons in the UI, but a human explicitly picking
        # it anyway is exactly the override the execute-time headroom check
        # now honors (see migration_workflow.py's confirm_override_headroom).
        # Insufficient memory headroom is a judgment call, not a hard
        # structural fact -- every workload should be displayed for choice
        # of handling regardless of headroom. manually_set below is what
        # actually carries that confirmation through to execution.

        destination_node = db.query(Node).filter(Node.id == payload.destination_node_id).one_or_none()
        if destination_node is None:
            raise HTTPException(404, "destination node not found")

        storage_rec = recommend_storage_for_candidate(
            db, destination_node.id, bool(item.get("currently_on_shared")), storage_preference=item.get("storage_preference"),
        )
        item["destination_node_id"] = str(destination_node.id)
        item["destination_node"] = destination_node.name
        item["destination_storage_id"] = storage_rec["id"] if storage_rec else None
        # Marks this item as a manual override so a later "Re-score" (see
        # rescore_migrate_plan() in placement.py) leaves it exactly as set
        # instead of recomputing a fresh pick for it -- re-scoring is meant to
        # refresh whatever's still at its auto-computed default, not silently
        # undo a choice you already made -- manual edits stay overrides.
        item["manually_set"] = True

    if payload.transport is not None:
        # Independent of destination -- doesn't mark manually_set, since
        # that flag exists purely to protect a destination override from
        # rescore_migrate_plan(); the transport choice was never something
        # rescore recomputes in the first place (it only touches
        # destination_node_id/destination_storage_id/candidates).
        item["transport"] = payload.transport

    ctx[plan_key] = plan
    op.context = ctx
    # dry_run_result carries its own copy of the same plan (that's what the
    # approval preview actually renders from) -- keep it in lockstep so the
    # UI never shows a stale destination for an edit that already saved.
    if op.dry_run_result and plan_key in op.dry_run_result:
        op.dry_run_result = {**op.dry_run_result, plan_key: plan}
    db.commit()
    db.refresh(op)
    return _serialize(op)


@router.post("/{operation_id}/rescore", dependencies=[Depends(require_admin)])
def rescore_migrate_plan_endpoint(operation_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    plan_key = _PLAN_KEY_BY_TYPE.get(op.operation_type_id)
    if plan_key is None:
        raise HTTPException(400, f"{op.operation_type_id} has no editable migration plan")
    if op.status != "awaiting_approval":
        raise HTTPException(409, "the plan can only be re-scored while it's awaiting approval")

    ctx = dict(op.context or {})
    # Same deep-copy requirement as update_migrate_plan_destination above --
    # rescore_migrate_plan() mutates item dicts in place, so a shallow copy
    # here would hit the identical silent-no-UPDATE bug.
    plan = copy.deepcopy(ctx.get(plan_key) or [])
    plan = placement.rescore_migrate_plan(db, plan)

    ctx[plan_key] = plan
    op.context = ctx
    if op.dry_run_result and plan_key in op.dry_run_result:
        op.dry_run_result = {**op.dry_run_result, plan_key: plan}
    db.commit()
    db.refresh(op)
    return _serialize(op)


# Every context key that holds workloads the automated plan did NOT put in
# the main editable migrate/plan list, but which still carry a real (if
# possibly flagged) destination + an "included" opt-in toggle: shutdown_plan
# (a running workload with no clean auto-pick, defaults to shutting down in
# place) and stopped_relocation_required (a stopped workload that never
# NEEDS to move, offered anyway in case the node is leaving the cluster for
# good). Same shape, same endpoint -- whichever list actually contains the
# workload_id is the one that gets updated: a stopped workload should
# still have the option to move if maintenance includes removing the
# host from the cluster, and every workload should be displayed for
# choice of handling regardless of headroom.
_OPTIONAL_PLAN_KEYS_BY_TYPE = {
    "node.enter_maintenance": ["shutdown_plan", "stopped_relocation_required"],
    "maintenance.run": ["shutdown_plan", "stopped_relocation_required"],
    "node.evacuate": ["stopped_relocation_required"],
}


class StoppedRelocationUpdateRequest(BaseModel):
    included: bool | None = None
    destination_node_id: uuid.UUID | None = None
    transport: str | None = None


@router.post("/{operation_id}/stopped-relocation/{workload_id}", dependencies=[Depends(require_admin)])
def update_stopped_relocation(
    operation_id: uuid.UUID, workload_id: uuid.UUID, payload: StoppedRelocationUpdateRequest,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    if payload.included is None and payload.destination_node_id is None and payload.transport is None:
        raise HTTPException(400, "nothing to update -- pass included, destination_node_id, and/or transport")
    if payload.transport is not None and payload.transport not in ("live", "offline"):
        raise HTTPException(400, "transport must be 'live' or 'offline'")

    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    candidate_keys = _OPTIONAL_PLAN_KEYS_BY_TYPE.get(op.operation_type_id) or []
    if not candidate_keys:
        raise HTTPException(400, f"{op.operation_type_id} has no optional relocation list")
    if op.status != "awaiting_approval":
        raise HTTPException(409, "this can only be changed while the operation is awaiting approval")

    ctx = dict(op.context or {})
    plan_key = next((k for k in candidate_keys if any(p.get("workload_id") == str(workload_id) for p in (ctx.get(k) or []))), None)
    if plan_key is None:
        raise HTTPException(404, "that workload isn't in this operation's optional relocation list")
    plan = copy.deepcopy(ctx.get(plan_key) or [])
    item = next((p for p in plan if p.get("workload_id") == str(workload_id)), None)

    if payload.destination_node_id is not None:
        candidate = next((c for c in item.get("candidates", []) if c.get("node_id") == str(payload.destination_node_id)), None)
        if candidate is None:
            raise HTTPException(400, "that node wasn't offered as a candidate for this workload")
        # Blocked candidates (most often: insufficient headroom, a resource
        # judgment call rather than a hard fact) are no longer refused --
        # picking one anyway is exactly the override the execute-time
        # headroom check now honors for an "included" row.
        destination_node = db.query(Node).filter(Node.id == payload.destination_node_id).one_or_none()
        if destination_node is None:
            raise HTTPException(404, "destination node not found")
        storage_rec = recommend_storage_for_candidate(db, destination_node.id, bool(item.get("currently_on_shared")))
        item["destination_node_id"] = str(destination_node.id)
        item["destination_node"] = destination_node.name
        item["destination_storage_id"] = storage_rec["id"] if storage_rec else None

    if payload.transport is not None:
        # Only meaningful for a currently-RUNNING workload (a shutdown_plan
        # row) -- an already-stopped one (stopped_relocation_required) has
        # no live state to keep running in the first place, so this is a
        # no-op there rather than an error -- the operator should still be
        # able to choose live vs. shutdown migration even when memory
        # headroom is insufficient; that choice is what was missing here.
        if item.get("currently_running"):
            item["transport"] = payload.transport

    if payload.included is not None:
        if payload.included and not item.get("destination_node_id"):
            raise HTTPException(400, "no valid destination is available for this workload -- can't include it")
        item["included"] = payload.included

    ctx[plan_key] = plan
    op.context = ctx
    if op.dry_run_result and plan_key in op.dry_run_result:
        op.dry_run_result = {**op.dry_run_result, plan_key: plan}
    db.commit()
    db.refresh(op)
    return _serialize(op)


# ---------------------------------------------------------------------------
# Stage W2-W6 dry-run entry points. Each mirrors the vm-migrations/dry-run
# pattern exactly: read-only, creates an Operation in dry_run/blocked/
# awaiting_approval, never mutates anything. Approval + execution all go
# through the same generic /{operation_id}/approve endpoint above.
# ---------------------------------------------------------------------------


class NodeIdRequest(BaseModel):
    node_id: uuid.UUID


@router.post("/node-evacuations/dry-run", dependencies=[Depends(require_admin)])
def create_node_evacuation_dry_run(payload: NodeIdRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.evacuation_workflow import EvacuationWorkflowError, dry_run_evacuation
    node = db.query(Node).filter(Node.id == payload.node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    existing = _existing_live_op(db, "node.evacuate", node.id)
    if existing is not None:
        return _serialize(existing)
    try:
        op = dry_run_evacuation(db, node, actor=user.email)
    except EvacuationWorkflowError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class HostUpdateRequest(NodeIdRequest):
    # True only from the "Check for Updates" button (a look, never an
    # approval); Apply Updates and Full Maintenance leave it False.
    check_only: bool = False


@router.post("/host-updates/dry-run", dependencies=[Depends(require_admin)])
def create_host_update_dry_run(payload: HostUpdateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.host_update_workflow import dry_run_host_update
    node = db.query(Node).filter(Node.id == payload.node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    existing = _existing_live_op(db, "host.update", node.id)
    if existing is not None:
        return _serialize(existing)
    op = dry_run_host_update(db, node, actor=user.email, check_only=payload.check_only)
    return _serialize(op)


@router.post("/host-reboots/dry-run", dependencies=[Depends(require_admin)])
def create_host_reboot_dry_run(payload: NodeIdRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.reboot_workflow import dry_run_reboot
    node = db.query(Node).filter(Node.id == payload.node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    existing = _existing_live_op(db, "host.reboot", node.id)
    if existing is not None:
        return _serialize(existing)
    op = dry_run_reboot(db, node, actor=user.email)
    return _serialize(op)


class MaintenanceRunRequest(BaseModel):
    node_id: uuid.UUID
    force_reboot: bool = False
    restore_after: bool = True


@router.post("/maintenance-runs/dry-run", dependencies=[Depends(require_admin)])
def create_maintenance_run_dry_run(payload: MaintenanceRunRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.maintenance_workflow import dry_run_maintenance
    node = db.query(Node).filter(Node.id == payload.node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    existing = _existing_live_op(db, "maintenance.run", node.id)
    if existing is not None:
        return _serialize(existing)
    op = dry_run_maintenance(db, node, actor=user.email, force_reboot=payload.force_reboot, restore_after=payload.restore_after)
    return _serialize(op)


class EnterMaintenanceRequest(BaseModel):
    node_id: uuid.UUID
    reason: str | None = None


@router.post("/node-maintenance/enter/dry-run", dependencies=[Depends(require_admin)])
def create_enter_maintenance_dry_run(payload: EnterMaintenanceRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.node_maintenance_workflow import dry_run_enter_maintenance
    node = db.query(Node).filter(Node.id == payload.node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    existing = _existing_live_op(db, "node.enter_maintenance", node.id)
    if existing is not None:
        return _serialize(existing)
    op = dry_run_enter_maintenance(db, node, actor=user.email, reason=payload.reason)
    return _serialize(op)


@router.post("/node-maintenance/exit/dry-run", dependencies=[Depends(require_admin)])
def create_exit_maintenance_dry_run(payload: NodeIdRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.node_maintenance_workflow import dry_run_exit_maintenance
    node = db.query(Node).filter(Node.id == payload.node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    existing = _existing_live_op(db, "node.exit_maintenance", node.id)
    if existing is not None:
        return _serialize(existing)
    op = dry_run_exit_maintenance(db, node, actor=user.email)
    return _serialize(op)


class BalanceRequest(BaseModel):
    node_ids: list[uuid.UUID] | None = None
    # Bulk Migrate: plan exactly these VMs (optionally all onto one node)
    # instead of running the balance scoring pass.
    workload_ids: list[uuid.UUID] | None = None
    destination_node_id: uuid.UUID | None = None
    # Balance Load page: throw away the plan that is waiting (never one that is running) and build a fresh one.
    replace: bool = False


def _record_manual_balance_check(db: Session, op: Operation, actor: str, node_ids) -> None:
    """A manual Balance Load preview goes into the same check history as the automatic checks. Never raises."""
    try:
        from pyxie_core.balance_config import balance_score
        from pyxie_core.balance_history import record_check
        res = op.dry_run_result or {}
        plan = [m for m in (res.get("migrate_plan") or []) if m.get("transport") != "skip"]
        q = db.query(Node).filter(Node.is_missing.is_(False))
        scoped = q.filter(Node.id.in_(node_ids)).all() if node_ids else []
        cluster_ids = {n.cluster_id for n in scoped} or {c.id for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all()}
        for cid in cluster_ids:
            nodes = q.filter(Node.cluster_id == cid).all()
            score = balance_score(nodes)
            record_check(
                db, cid, trigger="manual", actor=actor, result="plan_created" if plan else "no_moves",
                reason=None if plan else ((res.get("reasons") or [None])[-1]),
                balance_score=None if score is None else round(score), moves=len(plan), operation_id=op.id,
                details={
                    "rules": res.get("rules_review"),
                    "scope": "selected nodes" if node_ids else "whole cluster",
                    "plan": [{"name": m.get("name"), "from": m.get("source_node"), "to": m.get("destination_node"), "storage": m.get("current_storage"), "rule_fix": m.get("rule_fix")} for m in plan],
                    "refusals": {
                        "blocked_total": res.get("blocked_moves_total", 0),
                        "blocked": [{"name": b.get("name"), "from": b.get("source_node"), "to": b.get("blocked_node"), "why": (b.get("blocking_reasons") or [""])[0][:160], "gain": b.get("improvement")}
                                    for b in (res.get("blocked_moves") or [])[:5]],
                        "left_alone_total": res.get("left_alone_total", 0),
                    },
                },
            )
    except Exception:  # noqa: BLE001
        db.rollback()


@router.post("/cluster-rebalance/dry-run", dependencies=[Depends(require_admin)])
def create_balance_dry_run(payload: BalanceRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """"Balance Load" as one reviewable/editable plan -- see
    balance_workflow.py. node_ids, when given, restricts which workloads
    are evaluated as move candidates to those on the given node(s);
    destinations are never restricted to that set. Not tied to one node,
    so the idempotency guard checks for any other live cluster.rebalance
    regardless of scope."""
    from pyxie_core.balance_workflow import BalanceWorkflowError, dry_run_balance, dry_run_bulk_migrate
    existing = _existing_live_op(db, "cluster.rebalance", None)
    if payload.workload_ids:
        # Bulk Migrate shares the cluster.rebalance slot (one at a time). Unlike
        # Balance Load, silently handing back someone else's pending plan
        # would show the wrong VMs, so say so instead.
        if existing is not None:
            raise HTTPException(409, "a Balance Load or Bulk Migrate is already awaiting approval or running -- approve, cancel, or dismiss it first")
        try:
            op = dry_run_bulk_migrate(
                db, actor=user.email, workload_ids=payload.workload_ids,
                destination_node_id=payload.destination_node_id,
            )
        except BalanceWorkflowError as exc:
            raise HTTPException(400, str(exc))
        return _serialize(op)
    if existing is not None and payload.replace and existing.status == "awaiting_approval":
        existing.dismissed = True
        db.commit()
        write_audit_event(
            db, event_category="operation", event_type="cluster.rebalance.dismissed", actor=user.email, actor_type="user",
            metadata={"operation_id": str(existing.id), "reason": "replaced by a new Balance Load preview"},
        )
        existing = None
    if existing is not None:
        return _serialize(existing)
    op = dry_run_balance(db, actor=user.email, node_ids=payload.node_ids)
    _record_manual_balance_check(db, op, user.email, payload.node_ids)
    return _serialize(op)


class LifecycleActionRequest(BaseModel):
    workload_id: uuid.UUID
    action: str  # shutdown | start | force_stop | reboot
    timeout_seconds: int | None = None
    justification: str | None = None


@router.post("/workload-lifecycle/dry-run", dependencies=[Depends(require_admin)])
def create_lifecycle_dry_run(payload: LifecycleActionRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.lifecycle_workflow import (
        LifecycleWorkflowError, dry_run_force_stop, dry_run_reboot, dry_run_shutdown, dry_run_start,
    )
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        if payload.action == "shutdown":
            op = dry_run_shutdown(db, workload, actor=user.email, timeout_seconds=payload.timeout_seconds or 120)
        elif payload.action == "start":
            op = dry_run_start(db, workload, actor=user.email)
        elif payload.action == "force_stop":
            op = dry_run_force_stop(db, workload, actor=user.email, justification=payload.justification or "")
        elif payload.action == "reboot":
            op = dry_run_reboot(db, workload, actor=user.email, timeout_seconds=payload.timeout_seconds or 120)
        else:
            raise HTTPException(400, "action must be 'shutdown', 'start', 'force_stop', or 'reboot'")
    except LifecycleWorkflowError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class ResizeRequest(BaseModel):
    workload_id: uuid.UUID
    new_cores: int
    new_memory_mb: int


class StorageMoveRequest(BaseModel):
    workload_id: uuid.UUID
    target_storage: str


@router.post("/workload-storage-moves/dry-run", dependencies=[Depends(require_admin)])
def create_storage_move_dry_run(payload: StorageMoveRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.storage_move_workflow import StorageMoveError, dry_run_storage_move
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_storage_move(db, workload, target_storage=payload.target_storage, actor=user.email)
    except StorageMoveError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class SnapshotRequest(BaseModel):
    workload_id: uuid.UUID
    action: str  # create | delete | rollback
    name: str
    description: str | None = None
    include_ram: bool = False


class TagsRequest(BaseModel):
    workload_id: uuid.UUID
    tags: list[str]


@router.post("/workload-snapshots/dry-run", dependencies=[Depends(require_admin)])
def create_snapshot_dry_run(payload: SnapshotRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.snapshot_workflow import SnapshotError, dry_run_snapshot
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_snapshot(db, workload, action=payload.action, name=payload.name, description=payload.description or "",
                              include_ram=payload.include_ram, actor=user.email)
    except SnapshotError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-tags/dry-run", dependencies=[Depends(require_admin)])
def create_tags_dry_run(payload: TagsRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.tags_workflow import TagsError, dry_run_tags
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_tags(db, workload, tags=payload.tags, actor=user.email)
    except TagsError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class TagColorsRequest(BaseModel):
    cluster_id: uuid.UUID
    changes: dict[str, str | None]  # tag -> six-digit hex color, or null to go back to Proxmox's automatic color


@router.post("/cluster-tag-colors/dry-run", dependencies=[Depends(require_admin)])
def create_tag_colors_dry_run(payload: TagColorsRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.models import Cluster
    from pyxie_core.tag_colors_workflow import TagColorsError, dry_run_tag_colors
    cluster = db.query(Cluster).filter(Cluster.id == payload.cluster_id).one_or_none()
    if cluster is None:
        raise HTTPException(404, "cluster not found")
    try:
        op = dry_run_tag_colors(db, cluster, changes=payload.changes, actor=user.email)
    except TagColorsError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class DiskGrowRequest(BaseModel):
    workload_id: uuid.UUID
    disk: str
    new_size_gb: int


class HwChangeRequest(BaseModel):
    workload_id: uuid.UUID
    machine: str | None = None
    cpu_type: str | None = None
    snapshot_first: bool = True
    power_cycle: bool = False


class NicChangeRequest(BaseModel):
    workload_id: uuid.UUID
    action: str  # add | edit | remove
    net_id: str | None = None
    model: str | None = None
    bridge: str | None = None
    tag: int | None = None
    firewall: bool = False
    mac: str | None = None
    link_down: bool = False


class DiskChangeRequest(BaseModel):
    workload_id: uuid.UUID
    action: str  # add | detach | attach | delete_unused | move_to_vm
    key: str | None = None
    bus: str | None = None
    size_gb: int | None = None
    storage: str | None = None
    cache: str = "none"
    discard: bool = False
    ssd: bool = False
    iothread: bool = False
    target_workload_id: uuid.UUID | None = None
    confirm: str = ""
    backup: bool = True
    readonly: bool = False
    path: str | None = None


@router.post("/workload-disk-change/dry-run", dependencies=[Depends(require_admin)])
def create_disk_change_dry_run(payload: DiskChangeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.disk_workflow import DiskError, dry_run_disk_change
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_disk_change(db, workload, action=payload.action, key=payload.key, bus=payload.bus, size_gb=payload.size_gb, storage=payload.storage, cache=payload.cache,
                                 discard=payload.discard, ssd=payload.ssd, iothread=payload.iothread, target_workload_id=payload.target_workload_id, confirm=payload.confirm,
                                 backup=payload.backup, readonly=payload.readonly, path=payload.path, actor=user.email)
    except DiskError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-nic-change/dry-run", dependencies=[Depends(require_admin)])
def create_nic_change_dry_run(payload: NicChangeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.nic_workflow import NicError, dry_run_nic_change
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_nic_change(db, workload, action=payload.action, net_id=payload.net_id, model=payload.model, bridge=payload.bridge, tag=payload.tag,
                                firewall=payload.firewall, mac=payload.mac, link_down=payload.link_down, actor=user.email)
    except NicError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


class DeleteRequest(BaseModel):
    workload_id: uuid.UUID
    confirm_name: str = ""
    purge: bool = True
    destroy_unreferenced: bool = False


class CloneRequest(BaseModel):
    workload_id: uuid.UUID
    name: str
    newid: int | None = None
    target_node: str | None = None
    full: bool = True
    storage: str | None = None
    pool: str | None = None
    tags: list[str] | None = None
    start_after: bool = False


class CdromRequest(BaseModel):
    workload_id: uuid.UUID
    slot: str | None = None  # null = add a new drive
    volid: str | None = None  # null = eject
    boot_first: bool = False


class CtResizeRequest(BaseModel):
    workload_id: uuid.UUID
    cores: int | None = None
    memory_mb: int | None = None
    swap_mb: int | None = None


class CtNicRequest(BaseModel):
    workload_id: uuid.UUID
    action: str
    net_id: str | None = None
    bridge: str | None = None
    tag: int | None = None
    firewall: bool = False
    ip: str | None = None
    gateway: str | None = None
    mac: str | None = None


@router.post("/workload-ct-resize/dry-run", dependencies=[Depends(require_admin)])
def create_ct_resize_dry_run(payload: CtResizeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.ct_edit_workflow import CtEditError, dry_run_ct_resize
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_ct_resize(db, workload, cores=payload.cores, memory_mb=payload.memory_mb, swap_mb=payload.swap_mb, actor=user.email)
    except CtEditError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-ct-nic/dry-run", dependencies=[Depends(require_admin)])
def create_ct_nic_dry_run(payload: CtNicRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.ct_edit_workflow import CtEditError, dry_run_ct_nic
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_ct_nic(db, workload, action=payload.action, net_id=payload.net_id, bridge=payload.bridge, tag=payload.tag, firewall=payload.firewall, ip=payload.ip,
                            gateway=payload.gateway, mac=payload.mac, actor=user.email)
    except CtEditError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-cdrom/dry-run", dependencies=[Depends(require_admin)])
def create_cdrom_dry_run(payload: CdromRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.cdrom_workflow import CdromError, dry_run_cdrom
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_cdrom(db, workload, slot=payload.slot, volid=payload.volid, boot_first=payload.boot_first, actor=user.email)
    except CdromError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-clone/dry-run", dependencies=[Depends(require_admin)])
def create_clone_dry_run(payload: CloneRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.clone_workflow import CloneError, dry_run_clone
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_clone(db, workload, name=payload.name, newid=payload.newid, target_node=payload.target_node, full=payload.full, storage=payload.storage, pool=payload.pool,
                           tags=payload.tags, start_after=payload.start_after, actor=user.email)
    except CloneError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-delete/dry-run", dependencies=[Depends(require_admin)])
def create_delete_dry_run(payload: DeleteRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.delete_workflow import DeleteError, dry_run_delete
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_delete(db, workload, confirm_name=payload.confirm_name, purge=payload.purge, destroy_unreferenced=payload.destroy_unreferenced, actor=user.email)
    except DeleteError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-disk-grow/dry-run", dependencies=[Depends(require_admin)])
def create_disk_grow_dry_run(payload: DiskGrowRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.hw_edit_workflow import HwEditError, dry_run_disk_grow
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_disk_grow(db, workload, disk=payload.disk, new_size_gb=payload.new_size_gb, actor=user.email)
    except HwEditError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-hw-change/dry-run", dependencies=[Depends(require_admin)])
def create_hw_change_dry_run(payload: HwChangeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.hw_edit_workflow import HwEditError, dry_run_hw_change
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_hw_change(db, workload, machine=payload.machine, cpu_type=payload.cpu_type, snapshot_first=payload.snapshot_first,
                               power_cycle=payload.power_cycle, actor=user.email)
    except HwEditError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)


@router.post("/workload-resizes/dry-run", dependencies=[Depends(require_admin)])
def create_resize_dry_run(payload: ResizeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.resize_workflow import ResizeWorkflowError, dry_run_resize
    workload = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
    if workload is None:
        raise HTTPException(404, "workload not found")
    try:
        op = dry_run_resize(db, workload, new_cores=payload.new_cores, new_memory_mb=payload.new_memory_mb, actor=user.email)
    except ResizeWorkflowError as exc:
        raise HTTPException(400, str(exc))
    return _serialize(op)
