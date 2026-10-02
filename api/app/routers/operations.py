import copy
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.discovery import build_pve_client
from pyxie_core.models import Cluster, Node, Operation, PveTarget, Storage, Workload
from pyxie_core.migration_workflow import MigrationWorkflowError, approve as approve_migration, dry_run_migration
from pyxie_core.audit import write_audit_event
from pyxie_core.locks import release_locks_for_operation
from pyxie_core.operations_engine import OperationError, TERMINAL_STATUSES, cancel_operation
from pyxie_core import placement
from pyxie_core.placement import get_cluster_storage_preference, is_currently_on_shared_storage, recommend_destinations, recommend_storage_for_candidate

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/operations", tags=["operations"], dependencies=[Depends(get_current_user)])


def _serialize(op: Operation) -> dict:
    return {
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
        "context": op.context,
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
    "workload.resize": 600,  # shutdown + config PUT + start, generous margin over each's own wait_for_task timeout
    "host.reboot": 1800,
    "host.update": 3900,  # comfortably above host_maintenance_client's 3600s apply() timeout
    "maintenance.run": 21600,  # 6h
    "node.enter_maintenance": 10800,  # 3h -- same evacuation shape as node.evacuate
    "node.exit_maintenance": 1800,
    "cluster.rebalance": 10800,  # 3h -- same evacuation shape as node.evacuate
    "protection.backup_membership": 120,  # synchronous PVE config write, no task to wait on
    "workload.network_vlan_change": 120,  # synchronous PVE config write, no task to wait on -- same shape
}


def _approve_for_type(db: Session, op: Operation, actor: str) -> Operation:
    if op.operation_type_id == "vm.live_migrate":
        return approve_migration(db, op, approved_by=actor)
    if op.operation_type_id == "node.evacuate":
        from pyxie_core.evacuation_workflow import approve as approve_evac
        return approve_evac(db, op, approved_by=actor)
    if op.operation_type_id in ("workload.shutdown", "workload.start", "workload.force_stop"):
        from pyxie_core.lifecycle_workflow import approve as approve_lifecycle
        return approve_lifecycle(db, op, approved_by=actor)
    if op.operation_type_id == "workload.resize":
        from pyxie_core.resize_workflow import approve as approve_resize
        return approve_resize(db, op, approved_by=actor)
    if op.operation_type_id == "host.reboot":
        from pyxie_core.reboot_workflow import approve as approve_reboot
        return approve_reboot(db, op, approved_by=actor)
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
    if payload.transport is not None and payload.transport not in ("live", "offline", "shutdown_in_place"):
        raise HTTPException(400, "transport must be 'live', 'offline', or 'shutdown_in_place'")

    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HTTPException(404, "operation not found")
    if payload.transport == "shutdown_in_place" and op.operation_type_id not in ("node.enter_maintenance", "maintenance.run"):
        # Only makes sense when the node stays in the cluster afterward --
        # node.evacuate/cluster.rebalance are about moving load OFF a node
        # for good, so there's nowhere to "restart it here" later.
        raise HTTPException(400, f"'shutdown_in_place' isn't available for {op.operation_type_id}")
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
    if existing is not None:
        return _serialize(existing)
    op = dry_run_balance(db, actor=user.email, node_ids=payload.node_ids)
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
