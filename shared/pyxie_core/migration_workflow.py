"""Stage W1: single-VM live migration. The first-ever PVE write capability
in this codebase.

Two entry points:
  - dry_run_migration(): read-only, builds an Operation in 'dry_run' then
    either 'blocked' or 'awaiting_approval'. Safe to call freely.
  - execute_migration(): only proceeds past a human approval
    (op.status == 'approved'). Resumable -- safe to call again after a
    worker/API/container restart; it picks up from whatever op.pve_upid /
    op.status already record rather than re-issuing the migration.

Full sequence, matching the Safety Contract exactly:
  dry run -> approve -> revalidate (live PVE, not cache) -> lock ->
  migrate -> monitor PVE task -> read back state from PVE -> verify ->
  audit. A 200/UPID from the migrate call is never treated as success on
  its own.
"""

import time

from sqlalchemy.orm import Session

# A pure in-memory live migration is RAM-bound and should finish quickly;
# 900s is generous for that case. A migration that ALSO relocates storage
# (destination_storage set) is disk-transfer-bound instead, and a fixed
# 900s ceiling is simply wrong for that -- proven live against this
# cluster: a single 80GB disk relocation legitimately took 942s (real PVE
# task log: "migration finished successfully"), yet PyXie's own 900s
# timeout fired first and marked the operation FAILED while the real
# migration was still succeeding underneath it. The floor below stays fast
# for the common case; storage relocations instead get a size-scaled
# estimate (using a deliberately conservative throughput floor -- this
# LAN measured ~85-100 MiB/s in practice, 20 MiB/s leaves real headroom)
# capped at a few hours so a truly stuck task still gets caught eventually.
MIGRATION_TIMEOUT_FLOOR_SECONDS = 900
MIGRATION_TIMEOUT_CAP_SECONDS = 14400  # 4 hours
MIGRATION_MIN_THROUGHPUT_BYTES_PER_SEC = 20 * 1024 * 1024  # 20 MiB/s, conservative
STORAGE_RELOCATION_BUFFER_SECONDS = 300  # RAM pre-copy + PVE overhead on top of the disk estimate


def _migration_timeout_seconds(ctx: dict) -> float:
    disk_bytes = ctx.get("expected_disk_bytes")
    if ctx.get("target_storage_name") and disk_bytes:
        estimate = disk_bytes / MIGRATION_MIN_THROUGHPUT_BYTES_PER_SEC + STORAGE_RELOCATION_BUFFER_SECONDS
        return min(max(estimate, MIGRATION_TIMEOUT_FLOOR_SECONDS), MIGRATION_TIMEOUT_CAP_SECONDS)
    return MIGRATION_TIMEOUT_FLOOR_SECONDS

from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .vm_liveness import liveness_block_reason, probe_vm
from .maintenance import (
    _has_pci_passthrough,
    _qemu_config,
    _workload_disk_storage_names,
    check_crs_affinity,
    check_cpu_compatibility,
    workload_node_local_disk_storages,
)
from .models import Cluster, Node, Operation, PveTarget, Storage, Workload
from .operations_engine import (
    OperationError,
    approve_operation,
    block_operation,
    create_operation,
    enter_stage,
    fail_operation,
)
from .placement import evaluate_affinity, get_node_tier
from .preconditions import revalidate_live_migration
from .pve_write_client import (
    MutationsDisabledError,
    PveMaintenanceClient,
    SelfProtectionError,
    TaskTimeoutError,
    parse_migration_progress,
)
from . import rollback


class MigrationWorkflowError(Exception):
    pass


def _load_context(db: Session, workload: Workload):
    node = db.query(Node).filter(Node.id == workload.node_id).one()
    cluster = db.query(Cluster).filter(Cluster.id == workload.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return node, cluster, target


def _evaluate_migration_hard_blocks(
    db: Session, client, workload: Workload, source_node: Node, destination_node: Node,
    destination_storage: Storage | None, transport: str, was_running: bool, confirm_override_headroom: bool,
) -> tuple[list[str], list[str], dict]:
    """The full set of placement/eligibility hard-block checks for migrating
    `workload` from `source_node` to `destination_node` -- shared verbatim
    by dry_run_migration (preview) and execute_migration's revalidation
    step (immediately before the real PVE migrate call), so the two can
    never drift apart again. Before this, execution only re-checked
    quorum/online/VM-presence/live-headroom/conflicting-tasks via
    revalidate_live_migration() -- destination maintenance mode, trust
    tier, PyXie affinity, PVE HA/CRS rules, CPU compatibility, PCI
    passthrough, and storage locality were shown in the preview but never
    re-verified before actually migrating. `client` may be None (dry-run's
    own client-build can fail open with a soft warning); execution always passes a real,
    already-open client. Returns (reasons, blocking_rules, extra) where
    extra carries values a caller's own dry_run_result payload wants
    (currently just expected_disk_bytes)."""
    reasons: list[str] = []
    blocking_rules: list[str] = []
    extra: dict = {}

    if destination_node.maintenance_mode:
        reasons.append(f"{destination_node.name} is in maintenance mode -- not accepting new placements")
        blocking_rules.append("SAFE-MAINTMODE-001")

    # Live migration only ever makes sense against a currently-running guest.
    # Offline transport (shutdown -> migrate -> power back on) is the whole
    # point when a guest's disk sits on node-local storage -- a live block
    # migration there has to drive-mirror the disk WHILE the guest keeps
    # writing to it, which is markedly slower than copying it once with the
    # guest stopped -- live migration can be too costly time-wise with
    # node-local storage, so offline only requires the guest be in a sane
    # state to move at all, not that it stay running.
    if transport == "live" and not was_running:
        reasons.append(f"workload is not running (status={workload.status}); live migration requires a running VM")
        blocking_rules.append("SAFE-MIGRATE-001")
    elif transport == "offline" and workload.status not in ("running", "stopped"):
        reasons.append(f"workload status is '{workload.status}' -- not safe to migrate right now")
        blocking_rules.append("SAFE-MIGRATE-001")

    # A running VM whose QEMU does not answer would hang the move (live) or its shutdown (offline).
    if client and was_running:
        live_reason = liveness_block_reason(workload.vmid, probe_vm(client, source_node.name, workload.vmid))
        if live_reason:
            reasons.append(live_reason)
            blocking_rules.append("SAFE-MIGRATE-001")

    # Mirrors workload.resize's own SAFE-DOWNTIME-001 check -- offline
    # transport powers a running guest off for the duration of the disk
    # copy, same as a resize power-cycle, so a guest tagged as unable to
    # tolerate that needs the same explicit-review gate.
    if transport == "offline" and was_running and workload.downtime_tolerance == "low":
        reasons.append(
            "workload is tagged downtime_tolerance='low' (cannot tolerate downtime) -- "
            "offline migration will power it off for the duration of the move, requires explicit review"
        )
        blocking_rules.append("SAFE-DOWNTIME-001")

    if workload.ha_state:
        reasons.append(f"HA state: {workload.ha_state} -- verify HA handles this migration before proceeding")
        blocking_rules.append("SAFE-HA-001")

    trust_tier = get_node_tier(db, destination_node.id, "trust")
    if workload.sensitivity == "restricted" and trust_tier == "low":
        reasons.append(f"workload is 'restricted' but {destination_node.name} is trust_tier=low")
        blocking_rules.append("SAFE-TRUST-001")
    if workload.downtime_tolerance == "low" and trust_tier == "low":
        reasons.append(f"workload has downtime_tolerance='low' (critical) but {destination_node.name} is trust_tier=low")
        blocking_rules.append("SAFE-TRUST-001")

    aff_blocked, aff_reasons, _aff_score = evaluate_affinity(db, workload, destination_node)
    if aff_blocked:
        reasons.extend(aff_reasons)
        blocking_rules.append("SAFE-AFFINITY-001")

    node_local_storage = True  # fail-closed default if the client/config path below can't run
    expected_disk_bytes = None
    if client:
        if destination_storage is not None:
            try:
                qlist = client.qemu_list(source_node.name)
                entry = next((v for v in qlist if v.get("vmid") == workload.vmid), None)
                expected_disk_bytes = entry.get("maxdisk") if entry else None
            except Exception:
                expected_disk_bytes = None
        config = _qemu_config(client, source_node.name, workload)
        current_storage_names = sorted(_workload_disk_storage_names(config)) if config else []
        extra["current_storage"] = current_storage_names[0] if current_storage_names else None
        node_local_disks = workload_node_local_disk_storages(db, source_node, config)
        node_local_storage = node_local_disks is None or len(node_local_disks) > 0
        if node_local_storage:
            if destination_storage is not None:
                # An explicit destination storage was chosen -- this is
                # a valid live-storage-migration (with-local-disks),
                # not a blocker. Surfaced as information, not a SAFE-*
                # rule, since the user has already made this decision.
                if node_local_disks:
                    reasons.append(
                        f"disk(s) on node-local storage ({', '.join(node_local_disks)}); "
                        f"will be relocated to '{destination_storage.name}' as part of this {transport} migration"
                    )
                else:
                    reasons.append(
                        "could not confirm disk storage locality from live PVE config; "
                        "proceeding with the requested storage relocation, but treat as unverified"
                    )
            elif node_local_disks:
                reasons.append(
                    f"disk(s) on node-local storage ({', '.join(node_local_disks)}); migration "
                    f"requires either shared storage or an explicit destination storage to relocate to"
                )
                blocking_rules.append("SAFE-STORAGE-001")
            else:
                reasons.append(
                    "could not confirm disk storage locality from live PVE config; "
                    "treating conservatively as requiring offline migration"
                )
                blocking_rules.append("SAFE-STORAGE-001")

        passthrough = _has_pci_passthrough(config)
        if passthrough:
            reasons.append("PCI/device passthrough configured -- destination must have the same device")
            blocking_rules.append("SAFE-MIGRATE-001")

        # CRS/affinity and CPU compatibility are about node placement,
        # not storage -- they apply regardless of whether this is a
        # plain compute move or one that also relocates storage.
        crs_reasons = check_crs_affinity(client, workload, [destination_node.name])
        if crs_reasons:
            reasons.extend(crs_reasons)
            blocking_rules.append("SAFE-CRS-001")

        cpu_reasons = check_cpu_compatibility(client, source_node.name, destination_node.name, workload)
        if cpu_reasons:
            reasons.extend(cpu_reasons)
            blocking_rules.append("SAFE-CPU-001")

        if destination_node.mem_total_bytes:
            allocated = sum(
                (w.memory_bytes or 0)
                for w in db.query(Workload).filter(
                    Workload.node_id == destination_node.id, Workload.is_missing.is_(False),
                    Workload.status == "running",
                ).all()
            )
            dest_headroom = destination_node.mem_total_bytes - allocated
            if workload.memory_bytes and dest_headroom < workload.memory_bytes:
                reasons.append(
                    f"destination {destination_node.name} does not have enough memory headroom "
                    f"({dest_headroom} bytes free, needs {workload.memory_bytes})"
                    + (" -- proceeding anyway, manually confirmed" if confirm_override_headroom else "")
                )
                # Headroom is the one SAFE-* check here that's a resource
                # judgment call, not a compatibility fact -- a batch plan
                # computes it as a snapshot that goes stale the moment a
                # human reorders work or accepts a tighter fit than the
                # automated placement would. Insufficient memory headroom
                # isn't a constant -- every workload should be displayed
                # for choice of handling regardless of headroom. A human
                # explicitly choosing this destination in the plan editor (or
                # opting a shutdown-plan/stopped-relocation item into a
                # move) is exactly that judgment call -- still surfaced
                # above as a reason, never silently hidden, just not a
                # hard veto anymore. Every OTHER check here (CPU compat,
                # affinity, trust tier, passthrough, HA) stays a hard
                # block regardless -- those are compatibility/safety
                # facts, not something a human should be waved past.
                if not confirm_override_headroom:
                    blocking_rules.append("SAFE-MIGRATE-001")
    else:
        reasons.append("could not build read client to run full checks")

    extra["expected_disk_bytes"] = expected_disk_bytes
    return reasons, blocking_rules, extra


def dry_run_migration(
    db: Session,
    workload: Workload,
    destination_node: Node,
    *,
    actor: str,
    destination_storage: Storage | None = None,
    transport: str = "live",
    confirm_override_headroom: bool = False,
    parent_operation_id=None,
    correlation_id=None,
) -> Operation:
    if workload.type != "vm":
        raise MigrationWorkflowError("only VM (qemu) live migration is implemented in Stage W1, not LXC")
    if transport not in ("live", "offline"):
        raise MigrationWorkflowError(f"unknown transport {transport!r} -- must be 'live' or 'offline'")

    source_node, cluster, target = _load_context(db, workload)
    if destination_node.cluster_id != cluster.id:
        raise MigrationWorkflowError("destination node is not in the same cluster as the workload")
    if destination_node.id == source_node.id:
        raise MigrationWorkflowError("destination node is the same as the current node")

    if destination_storage is not None:
        reachable = destination_storage.scope == "cluster-shared" or (
            destination_storage.scope == "node-local" and destination_storage.node_id == destination_node.id
        )
        if not reachable:
            raise MigrationWorkflowError(
                f"storage {destination_storage.name!r} is not reachable from destination node {destination_node.name!r}"
            )

    was_running = workload.status == "running"
    op = create_operation(
        db,
        "vm.live_migrate",
        cluster_id=cluster.id,
        node_id=destination_node.id,
        workload_id=workload.id,
        parent_operation_id=parent_operation_id,
        correlation_id=correlation_id,
        context={
            "source_node_id": str(source_node.id),
            "source_node": source_node.name,
            "target_node_id": str(destination_node.id),
            "target_node": destination_node.name,
            "vmid": workload.vmid,
            "expected_memory_bytes": workload.memory_bytes,
            "target_storage_name": destination_storage.name if destination_storage else None,
            "transport": transport,
            "was_running": was_running,
            # Persisted so execution's revalidation (which now re-runs this
            # same headroom check for real, see _evaluate_migration_hard_blocks)
            # replays the SAME override decision a human already made at
            # approval time, instead of silently re-blocking on headroom
            # that was already explicitly accepted -- this flag previously
            # only affected dry-run.
            "confirm_override_headroom": confirm_override_headroom,
        },
        created_by=actor,
    )

    client = None
    try:
        client, _cred = build_pve_client(db, target)
    except Exception:
        client = None

    if client:
        with client:
            reasons, blocking_rules, extra = _evaluate_migration_hard_blocks(
                db, client, workload, source_node, destination_node, destination_storage,
                transport, was_running, confirm_override_headroom,
            )
    else:
        reasons, blocking_rules, extra = _evaluate_migration_hard_blocks(
            db, None, workload, source_node, destination_node, destination_storage,
            transport, was_running, confirm_override_headroom,
        )

    expected_disk_bytes = extra.get("expected_disk_bytes")
    if expected_disk_bytes is not None:
        op.context = {**(op.context or {}), "expected_disk_bytes": expected_disk_bytes}
        db.commit()

    blocking_rules = sorted(set(blocking_rules))
    dry_run_result = {
        "eligible": len(blocking_rules) == 0,
        "reasons": reasons,
        "blocking_safety_rules": blocking_rules,
        "source_node": source_node.name,
        "target_node": destination_node.name,
        "target_storage": destination_storage.name if destination_storage else None,
        "current_storage": extra.get("current_storage"),
        "vmid": workload.vmid,
        "workload_name": workload.name,
        "transport": transport,
        "will_power_cycle": transport == "offline" and was_running,
    }

    if blocking_rules:
        return enter_stage(
            db, op, status="blocked", stage="dry_run",
            dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor,
        )

    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_migration(db: Session, operation_id) -> Operation:
    """Resumable. Safe to call again after a crash -- picks up from
    op.pve_upid / op.status rather than re-issuing the migration.

    Stage order depends on ctx["transport"]:
      - "live" (default, unchanged): approved -> revalidating -> executing
        -> monitoring -> verifying -> completed. The guest stays running
        throughout; PVE block-migrates any local disk WHILE the guest keeps
        writing to it.
      - "offline": approved -> revalidating -> shutting_down (skipped if the
        guest was already stopped -- see ctx["was_running"]) -> executing
        -> monitoring -> starting_up (skipped to match) -> verifying ->
        completed. Copies the disk once with the guest stopped instead of
        block-mirroring it live -- markedly faster for a node-local disk,
        at the cost of guest downtime for the copy. Mirrors
        workload.resize's own shutting_down/starting_up
        pattern and stage naming exactly.
    """
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise MigrationWorkflowError(f"operation {operation_id} not found")
    if op.status not in (
        "approved", "revalidating", "shutting_down", "executing", "monitoring", "starting_up", "verifying",
    ):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    workload = db.query(Workload).filter(Workload.id == op.workload_id).one()
    destination_node = db.query(Node).filter(Node.id == op.node_id).one()
    source_node, cluster, target = _load_context(db, workload)
    ctx = op.context or {}
    transport = ctx.get("transport", "live")
    # Legacy operations (created before this field existed) never had an
    # offline path available, so they were only ever created against a
    # running guest -- default True is the correct backfill for those.
    was_running = ctx.get("was_running", True)
    power_cycles = transport == "offline" and was_running

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                precheck = revalidate_live_migration(
                    read_client,
                    source_node=source_node.name,
                    vmid=workload.vmid,
                    target_node=destination_node.name,
                    expected_memory_bytes=ctx.get("expected_memory_bytes"),
                    require_running=was_running,
                )
                if not precheck.ok:
                    return block_operation(
                        db, op, blocking_safety_rules=["SAFE-MIGRATE-001"],
                        actor="system",
                    )

                # Re-run the SAME hard-block checks the preview showed --
                # destination maintenance mode, trust tier, PyXie affinity,
                # PVE HA/CRS rules, CPU compatibility, PCI passthrough,
                # storage locality, and headroom can all have changed
                # between approval and execution. revalidate_live_migration
                # above only covers quorum/online/VM-presence/live-headroom/
                # conflicting-tasks -- this closes the gap by sharing the exact
                # same check function dry_run_migration uses, so the two
                # cannot drift apart again.
                destination_storage = None
                target_storage_name = ctx.get("target_storage_name")
                if target_storage_name:
                    destination_storage = db.query(Storage).filter(
                        Storage.name == target_storage_name, Storage.cluster_id == cluster.id,
                    ).one_or_none()
                hard_reasons, hard_blocks, _extra = _evaluate_migration_hard_blocks(
                    db, read_client, workload, source_node, destination_node, destination_storage,
                    transport, was_running, bool(ctx.get("confirm_override_headroom", False)),
                )
                if hard_blocks:
                    # Recorded so a blocked-at-revalidation operation isn't as
                    # opaque as a blocked-at-precheck one -- an operator can
                    # see WHY, not just that it was blocked.
                    op.dry_run_result = {**(op.dry_run_result or {}), "revalidation_blocking_reasons": hard_reasons}
                    db.commit()
                    return block_operation(
                        db, op, blocking_safety_rules=sorted(set(hard_blocks)), actor="system",
                    )

            op.precondition_snapshot = precheck.snapshot
            db.commit()

            try:
                # ttl_seconds matches the same 4.5h margin already used for
                # this operation type's own RQ job timeout (see
                # api/app/routers/operations.py's _JOB_TIMEOUT_BY_TYPE
                # comment: "4.5h -- 30min margin over the 4h internal cap")
                # -- a storage-relocating migration can legitimately run
                # up to MIGRATION_TIMEOUT_CAP_SECONDS (4h). The DEFAULT_TTL
                # of 1h these calls previously used (no override) could
                # expire mid-migration, letting a second operation acquire
                # the same node/workload lock while the first was still
                # genuinely in flight.
                migration_lock_ttl = 16200
                acquire_lock(db, resource_type="node", resource_id=source_node.id, operation_id=op.id, reason="vm.live_migrate", ttl_seconds=migration_lock_ttl)
                acquire_lock(db, resource_type="node", resource_id=destination_node.id, operation_id=op.id, reason="vm.live_migrate", ttl_seconds=migration_lock_ttl)
                acquire_lock(db, resource_type="workload", resource_id=workload.id, operation_id=op.id, reason="vm.live_migrate", ttl_seconds=migration_lock_ttl)
            except LockContention as exc:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")

            next_status = "shutting_down" if power_cycles else "executing"
            op = enter_stage(db, op, status=next_status, stage=next_status)

        maintenance_creds = load_pve_credentials(db, target, "maintenance")

        if op.status == "shutting_down":
            if not op.pve_upid:
                with PveMaintenanceClient(maintenance_creds) as write_client:
                    upid = write_client.shutdown_vm(source_node.name, workload.vmid, timeout=120)
                op.pve_upid = upid
                db.commit()
            with PveMaintenanceClient(maintenance_creds) as write_client:
                task_result = write_client.wait_for_task(source_node.name, op.pve_upid, timeout=180)
            if not task_result.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"shutdown before offline migration did not succeed: {task_result.raw}")
            op.pve_upid = None  # clear so executing/starting_up each track their own UPID cleanly
            db.commit()
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing" and not op.pve_upid:
            with PveMaintenanceClient(maintenance_creds) as write_client:
                upid = write_client.migrate_vm(
                    source_node.name, workload.vmid, destination_node.name, online=(transport == "live"),
                    target_storage=ctx.get("target_storage_name"),
                )
            op.pve_upid = upid
            db.commit()
            op = enter_stage(db, op, status="monitoring", stage="monitoring", pve_upid=upid)

        if op.status in ("executing", "monitoring") and op.pve_upid:
            if op.status != "monitoring":
                op = enter_stage(db, op, status="monitoring", stage="monitoring")

            timeout_seconds = _migration_timeout_seconds(ctx)
            deadline = time.monotonic() + timeout_seconds
            last_log_n = 0
            task_result = None
            with PveMaintenanceClient(maintenance_creds) as write_client:
                while True:
                    task_result = write_client.task_status(source_node.name, op.pve_upid)
                    try:
                        log_lines = write_client.task_log(source_node.name, op.pve_upid, start=last_log_n)
                    except Exception:
                        log_lines = []
                    if log_lines:
                        last_log_n = max(l.get("n", last_log_n) for l in log_lines)
                        progress = parse_migration_progress(log_lines)
                        if progress:
                            op.progress = progress
                            db.commit()
                    if task_result.status != "running":
                        break
                    if time.monotonic() > deadline:
                        raise TaskTimeoutError(f"task {op.pve_upid} on {source_node.name} did not finish within {timeout_seconds:.0f}s")
                    time.sleep(2)

            op.pve_task_result = task_result.raw
            db.commit()
            if not task_result.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error=f"PVE task {op.pve_upid} did not succeed: {task_result.raw}",
                )
            op.pve_upid = None
            db.commit()
            next_status = "starting_up" if power_cycles else "verifying"
            op = enter_stage(db, op, status=next_status, stage=next_status)

        if op.status == "starting_up":
            if not op.pve_upid:
                with PveMaintenanceClient(maintenance_creds) as write_client:
                    upid = write_client.start_vm(destination_node.name, workload.vmid)
                op.pve_upid = upid
                db.commit()
            with PveMaintenanceClient(maintenance_creds) as write_client:
                task_result = write_client.wait_for_task(destination_node.name, op.pve_upid, timeout=180)
            if not task_result.succeeded:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error=f"power-on after offline migration did not succeed: {task_result.raw} -- "
                          f"the VM has already moved to {destination_node.name}, it just didn't start on its own",
                )
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            read_client, _cred = build_pve_client(db, target)
            with read_client:
                dest_vms = read_client.qemu_list(destination_node.name)
                source_vms = read_client.qemu_list(source_node.name)
            on_dest = next((v for v in dest_vms if int(v.get("vmid", -1)) == workload.vmid), None)
            still_on_source = next((v for v in source_vms if int(v.get("vmid", -1)) == workload.vmid), None)

            verification = {
                "on_destination": on_dest,
                "still_on_source": still_on_source,
            }
            op.verification_result = verification
            db.commit()

            if on_dest is None:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error="PVE task reported success but VM is not listed on the destination node -- read-back verification failed",
                )
            if still_on_source is not None:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error="PVE task reported success but VM is still listed on the source node -- read-back verification failed",
                )
            # A live migration (or an offline one that power-cycled) must
            # land running; an offline migration of a guest that was
            # already stopped is expected to land stopped -- same
            # was-it-running-before contract as workload.resize.
            expect_status = "running" if (transport == "live" or was_running) else "stopped"
            if on_dest.get("status") != expect_status:
                release_locks_for_operation(db, op.id)
                return fail_operation(
                    db, op,
                    error=f"VM migrated to destination but status is {on_dest.get('status')!r}, expected {expect_status!r}",
                )

            release_locks_for_operation(db, op.id)
            try:
                from .discovery import run_discovery
                run_discovery(db, target, actor="operation")
            except Exception:
                pass  # freshness-only; verification above is the actual proof, not this
            return enter_stage(
                db, op, status="completed", stage="audit",
                rollback_classification=rollback.AUTO_REVERSIBLE,
            )

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except (MutationsDisabledError, TaskTimeoutError, SelfProtectionError) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- always record, never silently drop a mutation attempt
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
