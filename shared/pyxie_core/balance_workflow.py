"""cluster.rebalance -- "Balance Load" as one reviewable/editable plan,
same Safety Contract shape as node.enter_maintenance/maintenance.run/
node.evacuate, instead of its old flow (propose N moves via
/api/recommendations/balance-load-plan, apply each individually with no
single approval and no cancel). Reuses _placement_recommendations()'s
scoring pass unchanged -- this is a thin wrapper around it that packages
the result as a migrate_plan and executes it the same way every other
batch migration workflow does.

Not tied to one node (node_ids may span several, or be None for
cluster-wide) -- Operation.node_id stays null; instead every node that
appears as a source or destination anywhere in the plan gets its own
lock, so this can't race with e.g. someone else starting maintenance on
one of those nodes mid-rebalance, and so two rebalances can't overlap on
the same node. Each child vm.live_migrate then locks its own source/
destination again as part of its own Safety Contract -- not a conflict,
since acquire_lock() already treats a lock held by an ancestor operation
as the same actor, not contention.
"""

from sqlalchemy.orm import Session

from .locks import LockContention, acquire_lock, release_locks_for_operation
from .migration_workflow import MigrationWorkflowError, approve as approve_migration, dry_run_migration, execute_migration
from .models import Node, Operation, Storage, Workload
from .labels import child_reason, vm_label
from .operations_engine import (
    OperationError,
    approve_operation,
    block_operation,
    check_cancel_requested,
    create_operation,
    enter_stage,
    fail_operation,
)
from .discovery import build_pve_client
from .maintenance import _has_pci_passthrough, _qemu_config
from .models import Cluster, PveTarget
from .placement import (
    note_planned_move,
    current_storage_name,
    get_cluster_storage_preference,
    is_currently_on_shared_storage,
    rank_with_simulated_load,
    recommend_destinations,
    recommend_storage_for_candidate,
)
from .recommendations import _placement_recommendations


class BalanceWorkflowError(Exception):
    pass


# The preview lists the refusals worth reading, not every guest an environment-wide check (say, an unreadable VM config) refused.
MAX_BLOCKED_SHOWN = 10


def drop_unhelpful_moves(nodes: list[dict], migrate_plan: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep a move only if it leaves the destination no fuller than the source is right now.

    The scorer ranks destinations by headroom and can happily send a big guest to a small, quiet node
    so that the small node ends up fuller than the one the guest left: that swaps the imbalance instead
    of curing it. Moves are checked in plan order against a running projection (each kept move counts
    for the next). nodes are {node_id, name, mem_total_bytes, mem_usage_pct}. Returns (kept, dropped) where
    each dropped entry is a blocked_moves-shaped dict with kind="no_gain"."""
    pct = {n["node_id"]: float(n["mem_usage_pct"]) for n in nodes}
    total = {n["node_id"]: n["mem_total_bytes"] for n in nodes}
    names = {n["node_id"]: n["name"] for n in nodes}
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in migrate_plan:
        src, dst = item.get("source_node_id"), item.get("destination_node_id")
        mem = int(item.get("memory_bytes") or 0)
        if src not in pct or dst not in pct or not mem:
            kept.append(item)
            continue
        dst_after = pct[dst] + mem / total[dst] * 100.0
        if dst_after > pct[src] + 0.05:
            dropped.append({
                "workload_id": item["workload_id"], "vmid": item.get("vmid"), "name": item.get("name"),
                "memory_bytes": mem, "source_node_id": src, "source_node": names[src],
                "blocked_node_id": dst, "blocked_node": names[dst],
                "blocking_reasons": [
                    f"it would leave {names[dst]} at {round(dst_after)}% memory, fuller than {names[src]} is now "
                    f"({round(pct[src])}%), so it would swap the imbalance instead of fixing it"
                ],
                "improvement": item.get("improvement") or 0.0, "planned_instead": None, "kind": "no_gain",
            })
            continue
        pct[src] -= mem / total[src] * 100.0
        pct[dst] = dst_after
        kept.append(item)
    return kept, dropped


def _projection_nodes(db: Session) -> list[dict]:
    nodes = [
        n for n in db.query(Node).filter(Node.is_missing.is_(False)).order_by(Node.name).all()
        if n.mem_total_bytes and n.mem_usage_pct is not None and n.status == "online" and not n.maintenance_mode
    ]
    return [{"node_id": str(n.id), "name": n.name, "mem_total_bytes": n.mem_total_bytes, "mem_usage_pct": n.mem_usage_pct} for n in nodes]


def _plan_balances_memory(db: Session, migrate_plan: list[dict]) -> bool:
    """False only when the cluster is being balanced on CPU alone: the memory guard would be the wrong test then."""
    from .balance_config import get_config, resolve_metric
    first = next((i for i in migrate_plan if i.get("source_node_id")), None)
    if first is None:
        return True
    node = db.query(Node).filter(Node.id == first["source_node_id"]).one_or_none()
    if node is None or node.cluster_id is None:
        return True
    cluster_nodes = db.query(Node).filter(Node.cluster_id == node.cluster_id, Node.is_missing.is_(False)).all()
    return resolve_metric(get_config(db, node.cluster_id)["metric"], cluster_nodes) != "cpu"


def project_node_memory(db: Session, migrate_plan: list[dict]) -> list[dict]:
    """Per-node memory today vs. after the plan's own moves, for the Balance Load preview.

    "Today" is each node's live memory use; "after" debits the allocated memory of every
    workload leaving it and credits every one arriving (lines set to "Don't move" are ignored).
    Only nodes that are online and not in maintenance are listed: those are the ones the balance
    is about. A projection, not a measurement: the guests' real use differs from what they are
    allocated, which is also what the planner's own simulation counts."""
    nodes = [
        n for n in db.query(Node).filter(Node.is_missing.is_(False)).order_by(Node.name).all()
        if n.mem_total_bytes and n.mem_usage_pct is not None and n.status == "online" and not n.maintenance_mode
    ]
    return projected_memory_rows(
        [{"node_id": str(n.id), "name": n.name, "mem_total_bytes": n.mem_total_bytes, "mem_usage_pct": n.mem_usage_pct} for n in nodes],
        migrate_plan,
    )


def projected_memory_rows(nodes: list[dict], migrate_plan: list[dict]) -> list[dict]:
    """Pure core of project_node_memory(): nodes are {node_id, name, mem_total_bytes, mem_usage_pct}."""
    delta: dict[str, int] = {}
    for item in plan_items_to_move(migrate_plan):
        mem = int(item.get("memory_bytes") or 0)
        if item.get("source_node_id"):
            delta[item["source_node_id"]] = delta.get(item["source_node_id"], 0) - mem
        delta[item["destination_node_id"]] = delta.get(item["destination_node_id"], 0) + mem
    out = []
    for n in nodes:
        used = n["mem_total_bytes"] * n["mem_usage_pct"] / 100.0
        after = max(0.0, used + delta.get(n["node_id"], 0))
        out.append({
            "node_id": n["node_id"], "name": n["name"], "mem_total_bytes": n["mem_total_bytes"],
            "before_pct": round(n["mem_usage_pct"], 1),
            "after_pct": round(min(after / n["mem_total_bytes"] * 100.0, 100.0), 1),
        })
    return out


def dry_run_balance(
    db: Session, *, actor: str, node_ids: list | None = None, cluster_id=None, extra_context: dict | None = None,
    min_improvement: float | None = None, max_moves: int | None = None, skip_workload_ids: set | None = None,
    only_if_moves: bool = False,
) -> Operation | None:
    """The plan is built first and the operation created after, so an automatic run that finds nothing
    (only_if_moves) leaves no empty operation behind."""
    source_node_ids = set(node_ids) if node_ids else None
    blocked_moves: list[dict] = []
    left_alone: list[dict] = []
    recs = _placement_recommendations(
        db, source_node_ids=source_node_ids, blocked_out=blocked_moves, left_alone_out=left_alone,
        cluster_ids={cluster_id} if cluster_id else None, min_improvement=min_improvement,
        max_moves=max_moves, skip_workload_ids=skip_workload_ids,
    )

    migrate_plan = []
    for r in recs:
        wl = db.query(Workload).filter(Workload.id == r["object_id"]).one_or_none()
        if wl is None or wl.is_missing:
            continue
        ev = r["evidence"]
        migrate_plan.append({
            "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
            "memory_bytes": wl.memory_bytes,
            "pinned_node": wl.preferred_node.name if wl.preferred_node_id and wl.preferred_node else None,
            "source_node_id": str(wl.node_id), "source_node": ev["current_node"],
            "improvement": ev.get("improvement"),
            "destination_node_id": ev["suggested_node_id"], "destination_node": ev["suggested_node"],
            "destination_storage_id": ev["suggested_storage"]["id"] if ev.get("suggested_storage") else None,
            "currently_on_shared": ev.get("currently_on_shared", False),
            "current_storage": ev.get("current_storage"),
            "storage_preference": ev.get("storage_preference"),
            "candidates": ev.get("candidates", []),
            # Per-VM live vs. shutdown/migrate/power-on choice, editable in
            # the same plan preview as the destination override -- default
            # to live so existing behavior doesn't change unless picked
            # (live migration can be too costly time-wise with node-local
            # storage, so an operator may want to switch this per item).
            "transport": "live",
        })

    if migrate_plan and _plan_balances_memory(db, migrate_plan):
        migrate_plan, no_gain = drop_unhelpful_moves(_projection_nodes(db), migrate_plan)
        if no_gain:
            dropped_ids = {d["workload_id"] for d in no_gain}
            for b in blocked_moves:  # "sent to X instead" is no longer true for a guest whose move was dropped
                if b["workload_id"] in dropped_ids:
                    b["planned_instead"] = None
            blocked_moves.extend(no_gain)

    if only_if_moves and not migrate_plan:
        return None
    op = create_operation(
        db, "cluster.rebalance", cluster_id=cluster_id,
        context={"node_ids": [str(n) for n in node_ids] if node_ids else None, **(extra_context or {})},
        created_by=actor,
    )

    reasons = (
        [f"{len(migrate_plan)} workload(s) would move to improve cluster balance -- each move is still reviewed "
         "and approved individually before it happens, same as every other migration here."]
        if migrate_plan else
        ["no move would make memory use more even right now -- see the list of refused moves for why."
         if any(b.get("kind") == "no_gain" for b in blocked_moves) else
         "nothing scores meaningfully better elsewhere right now -- already balanced."]
    )
    dry_run_result = {
        "node": None, "reasons": reasons, "eligible": True, "blocking_safety_rules": [],
        "migrate_plan": migrate_plan,
        "blocked_moves": sorted(blocked_moves, key=lambda b: -b["improvement"])[:MAX_BLOCKED_SHOWN],
        "blocked_moves_total": len(blocked_moves),
        "left_alone": sorted(left_alone, key=lambda x: -(x.get("memory_bytes") or 0))[:MAX_BLOCKED_SHOWN * 2],
        "left_alone_total": len(left_alone),
        "projected_memory": project_node_memory(db, migrate_plan),
    }
    ctx = {**(op.context or {}), "migrate_plan": migrate_plan}
    if not migrate_plan:
        op = enter_stage(db, op, status="dry_run", stage="preflight", dry_run_result=dry_run_result, context=ctx, actor=actor)
        return enter_stage(db, op, status="completed", stage="audit", actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="preflight", dry_run_result=dry_run_result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def dry_run_bulk_migrate(
    db: Session, *, actor: str, workload_ids: list, destination_node_id=None,
) -> Operation:
    """Bulk Migrate: the same reviewable/editable cluster.rebalance plan, but
    for exactly the VMs the operator ticked on the Maintenance page rather
    than whatever the balance scoring pass suggests. One line per VM (ranked
    destination, storage, live/offline), approved once, executed as resumable
    child migrations by execute_balance() -- no new execution path.

    destination_node_id None  -> best unblocked destination per VM, with each
        planned move counted against the next one's headroom (same batch
        simulation evacuation uses, so three VMs can't be planned onto a
        node that only fits two).
    destination_node_id given -> that node for every VM, but only where it is
        actually eligible; a VM it can't take is listed under
        skipped_workloads with the reason instead of being forced there.

    Smallest VMs are planned first so one large, slow move (a big database)
    doesn't hold up the rest and runs last by default; the order is just
    the list order, nothing else depends on it.
    """
    if not workload_ids:
        raise BalanceWorkflowError("no VMs selected")

    wanted = {str(w) for w in workload_ids}
    workloads = (
        db.query(Workload)
        .filter(Workload.id.in_(list(wanted)), Workload.is_missing.is_(False), Workload.type == "vm")
        .all()
    )
    found = {str(w.id) for w in workloads}
    skipped: list[dict] = [
        {"workload_id": wid, "vmid": None, "name": None, "reasons": ["not found, or no longer a VM in inventory"]}
        for wid in sorted(wanted - found)
    ]
    workloads.sort(key=lambda w: ((w.memory_bytes or 0), (w.name or "")))

    dest_node = None
    if destination_node_id is not None:
        dest_node = db.query(Node).filter(Node.id == destination_node_id, Node.is_missing.is_(False)).one_or_none()
        if dest_node is None:
            raise BalanceWorkflowError("the chosen destination node was not found")
        if dest_node.maintenance_mode:
            raise BalanceWorkflowError(f"{dest_node.name} is in maintenance mode -- exit it first, or pick another node")
        if dest_node.status != "online":
            raise BalanceWorkflowError(f"{dest_node.name} is not online")

    migrate_plan: list[dict] = []
    by_cluster: dict = {}
    for wl in workloads:
        by_cluster.setdefault(wl.cluster_id, []).append(wl)

    for cluster_id, cluster_wls in by_cluster.items():
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none() if cluster else None
        if cluster is None or target is None:
            for wl in cluster_wls:
                skipped.append({"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name, "reasons": ["its cluster or PVE target is not in inventory"]})
            continue
        try:
            client, _cred = build_pve_client(db, target)
        except Exception as exc:  # noqa: BLE001
            raise BalanceWorkflowError(f"could not build a PVE read client to plan the moves: {exc}") from exc

        with client:
            candidates_all = (
                db.query(Node)
                .filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False), Node.status == "online", Node.maintenance_mode.is_(False))
                .all()
            )
            nodes_by_id = {n.id: n for n in candidates_all}
            simulated_added_bytes: dict = {}

            for wl in cluster_wls:
                row = {"workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name}
                if dest_node is not None and dest_node.cluster_id != cluster.id:
                    skipped.append({**row, "reasons": [f"{dest_node.name} is in a different cluster"]})
                    continue
                if dest_node is not None and wl.node_id == dest_node.id:
                    skipped.append({**row, "reasons": [f"already on {dest_node.name}"]})
                    continue
                src = db.query(Node).filter(Node.id == wl.node_id).one_or_none()
                if src is None:
                    skipped.append({**row, "reasons": ["its current node is not in inventory"]})
                    continue
                running = wl.status == "running"
                if running and _has_pci_passthrough(_qemu_config(client, src.name, wl)):
                    skipped.append({**row, "reasons": ["PCI/device passthrough -- no safe automated path"]})
                    continue

                pool = [n for n in candidates_all if n.id != wl.node_id]
                ranked = recommend_destinations(db, client, wl, pool, simulated_added_bytes=simulated_added_bytes)
                ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)

                if dest_node is not None:
                    pick = next((c for c in ranked if str(c.node_id) == str(dest_node.id)), None)
                    if pick is None or pick.blocked:
                        why = (pick.blocking_reasons if pick else None) or [f"{dest_node.name} is not an eligible destination for this VM"]
                        skipped.append({**row, "reasons": list(why)})
                        continue
                else:
                    pick = next((c for c in ranked if not c.blocked), None)
                    if pick is None:
                        why = (ranked[0].blocking_reasons if ranked else None) or ["no eligible destination node"]
                        skipped.append({**row, "reasons": [f"no eligible destination -- {why[0]}"]})
                        continue

                destination = nodes_by_id[pick.node_id]
                note_planned_move(simulated_added_bytes, wl, destination.id)
                currently_on_shared = is_currently_on_shared_storage(client, src, wl, db)
                current_storage = current_storage_name(client, src, wl)
                effective_pref = wl.storage_preference or get_cluster_storage_preference(db, cluster.id)
                storage_rec = recommend_storage_for_candidate(db, destination.id, currently_on_shared, storage_preference=effective_pref)
                item = {
                    "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                    "destination_node_id": str(destination.id), "destination_node": destination.name,
                    "destination_storage_id": storage_rec["id"] if storage_rec else None,
                    "currently_on_shared": currently_on_shared,
                    "current_storage": current_storage,
                    "storage_preference": effective_pref,
                    "candidates": [
                        {
                            "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score,
                            "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                        }
                        for c in ranked
                    ],
                    # Running guests move live; a stopped one is just an
                    # offline move (cheap on shared storage). Both editable
                    # per line in the plan preview.
                    "transport": "live" if running else "offline",
                }
                if dest_node is not None:
                    # An explicit "send them all to X" is a manual pick, so a
                    # later Re-score leaves it alone instead of silently
                    # re-choosing; X was just verified eligible for this VM
                    # (including earlier moves in this batch) above.
                    item["manually_set"] = True
                migrate_plan.append(item)

    if not migrate_plan:
        detail = "; ".join(f"{(r.get('name') or r['workload_id'])}: {r['reasons'][0]}" for r in skipped[:6]) or "nothing to move"
        raise BalanceWorkflowError(f"none of the selected VMs can be moved -- {detail}")

    reasons = [
        f"{len(migrate_plan)} of {len(wanted)} selected VM(s) planned. Smallest first, largest last. "
        "Review and change any line below; nothing moves until you approve."
    ]
    for r in skipped[:12]:
        label = r.get("name") or r["workload_id"]
        vm = f" (vmid {r['vmid']})" if r.get("vmid") else ""
        reasons.append(f"Not planned: {label}{vm} -- {r['reasons'][0]}")
    if len(skipped) > 12:
        reasons.append(f"...and {len(skipped) - 12} more not planned.")

    op = create_operation(
        db, "cluster.rebalance",
        context={
            "mode": "bulk_migrate", "node_ids": None,
            "workload_ids": sorted(wanted),
            "destination_node_id": str(dest_node.id) if dest_node else None,
        },
        created_by=actor,
    )
    dry_run_result = {
        "node": None, "reasons": reasons, "eligible": True, "blocking_safety_rules": [],
        "migrate_plan": migrate_plan, "skipped_workloads": skipped, "mode": "bulk_migrate",
    }
    ctx = {**(op.context or {}), "migrate_plan": migrate_plan}
    op = enter_stage(db, op, status="dry_run", stage="preflight", dry_run_result=dry_run_result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def plan_items_to_move(plan: list[dict]) -> list[dict]:
    """The plan lines that will actually run: anything the operator set to
    "Don't move" (transport == "skip") on the preview is dropped."""
    return [item for item in plan if item.get("transport") != "skip"]


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_balance(db: Session, operation_id) -> Operation:
    """Resumable -- progress tracked in op.context exactly like every
    other batch migration workflow's own evacuate stage."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise BalanceWorkflowError(f"operation {operation_id} not found")

    ctx = op.context or {}
    migrate_plan = ctx.get("migrate_plan", [])
    completed_migrations = list(ctx.get("completed_migrations", []))

    def save_progress():
        op.context = {**ctx, "migrate_plan": migrate_plan, "completed_migrations": completed_migrations}
        db.commit()

    try:
        if op.status == "approved":
            involved_node_ids = set()
            for item in plan_items_to_move(migrate_plan):
                wl = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                if wl is not None:
                    involved_node_ids.add(wl.node_id)
                involved_node_ids.add(item["destination_node_id"])
            try:
                for node_id in involved_node_ids:
                    # 12600s (3.5h) matches this operation type's own RQ
                    # job timeout (10800s/3h) plus a 30min margin, same
                    # reasoning as node.evacuate.
                    acquire_lock(db, resource_type="node", resource_id=node_id, operation_id=op.id, reason="cluster.rebalance", ttl_seconds=12600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            op = enter_stage(db, op, status="evacuating", stage="evacuating")

        if op.status == "evacuating":
            for item in plan_items_to_move(migrate_plan):
                if item["workload_id"] in completed_migrations:
                    continue
                cancelled = check_cancel_requested(db, op)
                if cancelled:
                    return cancelled
                workload = db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
                destination_node = db.query(Node).filter(Node.id == item["destination_node_id"]).one_or_none()
                if workload is None or workload.is_missing or destination_node is None:
                    completed_migrations.append(item["workload_id"])
                    save_progress()
                    continue
                destination_storage = (
                    db.query(Storage).filter(Storage.id == item["destination_storage_id"]).one_or_none()
                    if item.get("destination_storage_id") else None
                )
                try:
                    child = dry_run_migration(
                        db, workload, destination_node, actor=f"cluster.rebalance:{op.id}",
                        destination_storage=destination_storage, transport=item.get("transport", "live"),
                        confirm_override_headroom=bool(item.get("manually_set")),
                        parent_operation_id=op.id, correlation_id=op.correlation_id,
                    )
                except MigrationWorkflowError as exc:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"could not plan migration for {vm_label(item)}: {exc}")
                if child.status == "blocked":
                    release_locks_for_operation(db, op.id)
                    return block_operation(db, op, blocking_safety_rules=(child.blocking_safety_rules or []) + ["SAFE-ROLLBACK-001"], actor="system")
                child = approve_migration(db, child, approved_by=f"cluster.rebalance:{op.id}")
                child = execute_migration(db, child.id)
                if child.status != "completed":
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"migration for {vm_label(item)} did not complete (status={child.status}): {child_reason(child)}")
                completed_migrations.append(item["workload_id"])
                save_progress()

            release_locks_for_operation(db, op.id)
            # No `return` here -- fall through to the `verifying` block below
            # in this same call, exactly like evacuation_workflow.py does.
            # This used to `return` right after the transition, which meant
            # the operation only ever finished if something re-invoked this
            # function afterward -- and nothing does except a worker restart's
            # resume_inflight_operations(). Every real "stuck in verifying"
            # incident (2026-09-13, three times) was this bug.
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            release_locks_for_operation(db, op.id)
            return enter_stage(db, op, status="completed", stage="audit")

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status}, stage={op.stage})")

    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error at stage {op.stage}: {exc}")
