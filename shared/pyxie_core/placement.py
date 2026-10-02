"""DRS-style placement recommendation engine.

Ranks candidate destination nodes for a workload's migration by:
  - cluster balance (prefer less-loaded nodes -- the actual "DRS" part)
  - node performance tier (manually tagged, suggested from hardware specs)
  - node trust tier vs. workload sensitivity (hard block, not just scoring --
    a 'restricted' workload can never land on a trust_tier=low node)
  - PyXie-level affinity/anti-affinity rules (independent of PVE's own HA
    affinity, which most workloads in a typical lab are never enrolled in)

This sits ON TOP OF the existing per-candidate eligibility gates already in
maintenance.py / migration_workflow.py (storage locality, CRS/HA, CPU
compat, PCI passthrough, memory headroom) -- it does not replace or
re-implement those; a node that fails an existing gate is still excluded
here, just reported as blocked rather than silently dropped.

Node tiers are stored as ordinary Policy rows (scope_type='node',
scope_id=<node id>, key='placement.performance_tier'/'placement.trust_tier')
-- reusing the existing generic policy store rather than adding new Node
columns, matching this app's "narrower scope, data not enum" philosophy.
"""

import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from .maintenance import (
    check_crs_affinity, check_cpu_compatibility, _has_pci_passthrough, _qemu_config,
    _workload_disk_storage_names, workload_node_local_disk_storages,
)
from .models import Node, PlacementAffinityRule, Policy, Storage, Workload

TIERS = ("low", "standard", "high")
# Deliberately small relative to the live-utilization balance score below
# (which now swings up to +/-60) -- tier is a tiebreaker/modifier among
# similarly-loaded nodes, not something that should override a genuinely
# less-loaded alternative just because a node is tagged 'high' -- real
# utilization is meant to be the dominant signal, tier secondary.
_TIER_SCORE = {"low": -5, "standard": 0, "high": 5}
_TRUST_SCORE = {"low": -2, "standard": 0, "high": 2}
# How strongly a workload's own downtime_tolerance amplifies (or dampens) its
# preference for higher performance/trust-tier nodes. A workload that can't
# tolerate downtime should actively compete for the best hosts; one that
# tolerates it easily should mostly defer to plain balance instead.
_DOWNTIME_TOLERANCE_MULTIPLIER = {"low": 2.5, "standard": 1.0, "high": 0.4}
# Large relative to tier (+/-5-7) and affinity (+/-10-20) soft scores, and
# comparable to the live-balance score's own max swing (+/-60) -- a
# workload's preferred host should win most real-world comparisons, not
# just nudge the ranking. Still a soft preference, not a hard pin -- see
# the hard-block gates above, which still apply unchanged.
_PREFERRED_HOST_SCORE = 40.0


def get_node_tier(db: Session, node_id, tier_key: str) -> str:
    """tier_key is 'performance' or 'trust'. Defaults to 'standard' if unset."""
    row = (
        db.query(Policy)
        .filter(Policy.scope_type == "node", Policy.scope_id == node_id, Policy.key == f"placement.{tier_key}_tier")
        .one_or_none()
    )
    value = row.value if row else "standard"
    return value if value in TIERS else "standard"


def get_cluster_storage_preference(db: Session, cluster_id) -> str | None:
    """Cluster-level fallback for Workload.storage_preference, same policy
    store as the node tiers above (scope_type='cluster' here). None means
    "no default set" -- a real, distinct state from 'local'/'shared', not
    an error -- callers should fall back further (today: infer from the
    workload's current location) rather than treating None as 'local'.
    Opt-in only, deliberately: an existing cluster that's never had this
    set sees no behavior change."""
    row = (
        db.query(Policy)
        .filter(Policy.scope_type == "cluster", Policy.scope_id == cluster_id, Policy.key == "placement.storage_preference")
        .one_or_none()
    )
    value = row.value if row else None
    return value if value in ("local", "shared") else None


def get_node_default_storage_id(db: Session, node_id) -> str | None:
    """Per-node override for WHICH of that node's own local storage pools
    a migration should use, when a workload's preference calls for local
    storage there. Same policy store as the tiers above (scope_type='node'
    here). None means "no override" -- recommend_storage_for_candidate()
    falls back to auto-picking whichever local storage has the most free
    space, same as before this existed. The Nodes and Maintenance pages
    used to show that auto-picked storage as read-only; this makes it an
    actual, settable choice from both places."""
    row = (
        db.query(Policy)
        .filter(Policy.scope_type == "node", Policy.scope_id == node_id, Policy.key == "placement.default_storage_id")
        .one_or_none()
    )
    return row.value if row and row.value else None


def get_node_note(db: Session, node_id) -> str:
    """PyXie-only free-text note explaining WHY a node's tiers are set the
    way they are -- never synced from/to PVE. Reuses the generic policies
    store (key='placement.notes'), same as the tiers themselves."""
    row = (
        db.query(Policy)
        .filter(Policy.scope_type == "node", Policy.scope_id == node_id, Policy.key == "placement.notes")
        .one_or_none()
    )
    return row.value if row and isinstance(row.value, str) else ""


def suggest_performance_tier(client, nodes: list[Node]) -> dict:
    """Best-effort suggestion from live PVE hardware info (core count is the
    only reliably-present signal across PVE versions/hardware). Ranks nodes
    into terciles within THIS cluster -- 'high'/'low' are relative to your
    other nodes, not an absolute judgment. Returns {node_id: {suggested, cpu_cores, cpu_model}}.
    Never writes anything -- purely informational, the admin still sets the
    real value via the ordinary policy endpoint.
    """
    specs = {}
    for n in nodes:
        try:
            status = client.node_status(n.name) or {}
            cpuinfo = status.get("cpuinfo") or {}
            specs[n.id] = {"cpu_cores": cpuinfo.get("cpus"), "cpu_model": cpuinfo.get("model")}
        except Exception:
            specs[n.id] = {"cpu_cores": None, "cpu_model": None}

    known = sorted([s for s in specs.values() if s["cpu_cores"]], key=lambda s: s["cpu_cores"])
    if len(known) < 2:
        for s in specs.values():
            s["suggested"] = "standard"
        return specs

    low_cut = known[len(known) // 3]["cpu_cores"]
    high_cut = known[-(len(known) // 3) - 1]["cpu_cores"]
    for s in specs.values():
        cores = s["cpu_cores"]
        if cores is None:
            s["suggested"] = "standard"
        elif cores >= high_cut and high_cut != low_cut:
            s["suggested"] = "high"
        elif cores <= low_cut and high_cut != low_cut:
            s["suggested"] = "low"
        else:
            s["suggested"] = "standard"
    return specs


def _affinity_rules_for_workload(db: Session, workload: Workload) -> list[PlacementAffinityRule]:
    wl_id_str = str(workload.id)
    tag_rules = []
    if workload.tags:
        tag_rules = (
            db.query(PlacementAffinityRule)
            .filter(PlacementAffinityRule.scope_type == "tag_group", PlacementAffinityRule.tag.in_(workload.tags))
            .all()
        )
    pair_rules = [
        r for r in db.query(PlacementAffinityRule).filter(PlacementAffinityRule.scope_type == "workload_pair").all()
        if r.workload_ids and wl_id_str in [str(x) for x in r.workload_ids]
    ]
    return tag_rules + pair_rules


def _partner_workload_ids(db: Session, rule: PlacementAffinityRule, workload: Workload) -> set[str]:
    """The other workload id(s) this rule links to `workload`."""
    if rule.scope_type == "workload_pair":
        return {str(w) for w in (rule.workload_ids or []) if str(w) != str(workload.id)}
    # tag_group: every OTHER workload sharing the tag
    partners = (
        db.query(Workload)
        .filter(Workload.id != workload.id, Workload.is_missing.is_(False))
        .all()
    )
    return {str(w.id) for w in partners if w.tags and rule.tag in w.tags}


# Reserved key inside the batch's `simulated_added_bytes` tally that carries the
# placements already decided EARLIER IN THE SAME PLAN ({workload_id: node_id}), so
# affinity rules see them too. Without it every item was checked against where
# things are TODAY, and a hard keep-apart rule still let a plan send both
# databases to the same empty node (CRE-QADB2 + CRE-StgDB2 -> m401, 2026-10-02).
PLANNED_KEY = "__planned_placements__"


def note_planned_move(simulated_added_bytes: dict, workload, node_id) -> None:
    """Record a planned move: memory debited from the destination AND the
    workload's new location for affinity checks."""
    simulated_added_bytes[node_id] = simulated_added_bytes.get(node_id, 0) + (workload.memory_bytes or 0)
    simulated_added_bytes.setdefault(PLANNED_KEY, {})[str(workload.id)] = str(node_id)


def residents_with_planned(resident_ids: set, planned: dict | None, candidate_node_id) -> set:
    """Who will be on `candidate_node_id` once the already-planned moves happen."""
    out = set(resident_ids)
    for wid, nid in (planned or {}).items():
        if str(nid) == str(candidate_node_id):
            out.add(str(wid))
        else:
            out.discard(str(wid))
    return out


def evaluate_affinity(
    db: Session, workload: Workload, candidate_node: Node, planned: dict | None = None
) -> tuple[bool, list[str], float]:
    """Returns (blocked, reasons, soft_score_delta) for placing `workload` on
    `candidate_node`, given every affinity rule referencing it."""
    rules = _affinity_rules_for_workload(db, workload)
    if not rules:
        return False, [], 0.0

    resident_ids = {
        str(w.id)
        for w in db.query(Workload).filter(Workload.node_id == candidate_node.id, Workload.is_missing.is_(False)).all()
    }
    resident_ids = residents_with_planned(resident_ids, planned, candidate_node.id)

    blocked = False
    reasons: list[str] = []
    score_delta = 0.0
    for rule in rules:
        partners = _partner_workload_ids(db, rule, workload)
        partner_here = bool(partners & resident_ids)
        if rule.rule_type == "keep_apart" and partner_here:
            msg = rule.description or f"keep_apart rule: a linked workload is already on {candidate_node.name}"
            if rule.strict:
                blocked = True
                reasons.append(f"BLOCKED: {msg}")
            else:
                score_delta -= 20
                reasons.append(f"soft penalty: {msg}")
        elif rule.rule_type == "keep_together" and partners and not partner_here:
            msg = rule.description or "keep_together rule: no linked workload is on this node"
            score_delta -= 10 if rule.strict else 5
            reasons.append(f"note: {msg}")
        elif rule.rule_type == "keep_together" and partner_here:
            score_delta += 10
            reasons.append(f"linked workload already here (keep_together satisfied)")
    return blocked, reasons, score_delta


@dataclass
class DestinationCandidate:
    node_id: object
    node_name: str
    blocked: bool
    blocking_reasons: list[str]
    score: float
    reasons: list[str]


def recommend_destinations(
    db: Session, client, workload: Workload, candidate_nodes: list[Node],
    *, simulated_added_bytes: dict | None = None,
) -> list[DestinationCandidate]:
    """simulated_added_bytes, when given, is a running {node_id: bytes}
    tally of memory already committed to each candidate EARLIER IN THIS
    SAME BATCH (before their real telemetry/allocation catches up) --
    debited from headroom here, a real hard-block input, not just a soft
    scoring nudge (see rank_with_simulated_load() below for the score
    side). Without this, the headroom check only ever sees the DB's
    current real allocation, so a batch could plan 3 VMs onto a node
    whose real headroom only fits 2 -- the ranking would spread them out
    somewhat via score alone, but nothing here would actually REFUSE the
    3rd if its score still won, so it would only get caught later, at
    that VM's own execution-time revalidation, well after the plan was
    already shown as clean -- every already-planned move in the same
    batch should count against a destination's headroom, not just its
    score."""
    source_node = db.query(Node).filter(Node.id == workload.node_id).one()
    config = _qemu_config(client, source_node.name, workload) if client else None
    passthrough = _has_pci_passthrough(config)

    results = []
    for node in candidate_nodes:
        blocked = False
        blocking_reasons: list[str] = []
        reasons: list[str] = []
        score = 50.0  # neutral baseline

        # -- existing eligibility gates, reused as hard blocks --
        if passthrough:
            blocked = True
            blocking_reasons.append("PCI/device passthrough configured")

        allocated = sum(
            (w.memory_bytes or 0)
            for w in db.query(Workload).filter(
                Workload.node_id == node.id, Workload.is_missing.is_(False), Workload.status == "running",
            ).all()
        )
        allocated += (simulated_added_bytes or {}).get(node.id, 0)
        headroom = (node.mem_total_bytes - allocated) if node.mem_total_bytes else None
        if headroom is not None and workload.memory_bytes and headroom < workload.memory_bytes:
            blocked = True
            blocking_reasons.append(
                f"insufficient memory headroom ({headroom} bytes free"
                + (", including other moves already planned onto it in this same batch" if (simulated_added_bytes or {}).get(node.id) else "")
                + ")"
            )

        if client and not blocked:
            crs_reasons = check_crs_affinity(client, workload, [node.name])
            if crs_reasons:
                blocked = True
                blocking_reasons.extend(crs_reasons)
            cpu_reasons = check_cpu_compatibility(client, source_node.name, node.name, workload)
            if cpu_reasons:
                blocked = True
                blocking_reasons.extend(cpu_reasons)

        # -- trust tier vs. sensitivity/downtime_tolerance: hard block --
        # A workload that can't tolerate downtime shares the same guard rail
        # as a security-restricted one: neither belongs on your least-
        # trusted node, just for different reasons (data exposure vs.
        # operational stability/reliability confidence).
        trust_tier = get_node_tier(db, node.id, "trust")
        if workload.sensitivity == "restricted" and trust_tier == "low":
            blocked = True
            blocking_reasons.append(f"workload is 'restricted' but {node.name} is trust_tier=low (SAFE-TRUST-001)")
        if workload.downtime_tolerance == "low" and trust_tier == "low":
            blocked = True
            blocking_reasons.append(
                f"workload has downtime_tolerance='low' (critical) but {node.name} is trust_tier=low (SAFE-TRUST-001)"
            )

        # -- PyXie affinity rules --
        aff_blocked, aff_reasons, aff_score = evaluate_affinity(db, workload, node, (simulated_added_bytes or {}).get(PLANNED_KEY))
        if aff_blocked:
            blocked = True
        blocking_reasons.extend(r for r in aff_reasons if r.startswith("BLOCKED"))
        reasons.extend(r for r in aff_reasons if not r.startswith("BLOCKED"))
        score += aff_score

        # -- preferred host: a soft per-workload preference (Workload.
        # preferred_node_id), not a hard pin -- large enough to reliably win
        # over ordinary balance/tier differences, but every real hard block
        # above (headroom, CRS/CPU, trust tier, affinity) still excludes this
        # node the same as any other candidate; there is no override here.
        if workload.preferred_node_id and str(workload.preferred_node_id) == str(node.id):
            score += _PREFERRED_HOST_SCORE
            reasons.append(f"preferred host ({node.name})")

        # -- cluster balance: prefer the LEAST-loaded eligible node, using
        # real live host telemetry (cpu_usage_pct/mem_usage_pct, already
        # polled every discovery cycle) rather than a configured-allocation
        # estimate. Allocation-only headroom systematically favors any node
        # with more total RAM regardless of its actual current load (a node
        # with 2x the RAM of its peers "looks" roomier by allocation alone
        # even while running hotter than they are) -- found for real
        # 2026-09-11 when a node explicitly tagged performance_tier=high
        # also had the most total RAM and won placement on that basis alone.
        # Falls back to the allocation-based % only when live stats aren't
        # populated yet (node just discovered, no poll cycle completed).
        if node.cpu_usage_pct is not None and node.mem_usage_pct is not None:
            free_pct = 100 - ((node.cpu_usage_pct + node.mem_usage_pct) / 2)
            score += (free_pct - 50) * 1.2  # centered so a 50%-free node is neutral
            reasons.append(f"{round(free_pct)}% free (live CPU+RAM)")
        elif node.mem_total_bytes and headroom is not None:
            balance_pct = 100 * headroom / node.mem_total_bytes
            score += (balance_pct - 50) * 1.2
            reasons.append(f"{round(balance_pct)}% memory headroom (no live stats yet)")

        # -- performance/trust tier bias, AMPLIFIED for workloads that can't
        # tolerate downtime (they should gravitate to your best/most-trusted
        # nodes) and DAMPENED for freely-tolerant ones (they should mostly
        # just go wherever helps balance, not compete for the best hosts) --
        perf_tier = get_node_tier(db, node.id, "performance")
        multiplier = _DOWNTIME_TOLERANCE_MULTIPLIER[workload.downtime_tolerance]
        score += (_TIER_SCORE[perf_tier] + _TRUST_SCORE[trust_tier]) * multiplier
        reasons.append(f"performance tier: {perf_tier}")
        reasons.append(f"trust tier: {trust_tier}")
        if workload.downtime_tolerance != "standard":
            reasons.append(f"downtime tolerance: {workload.downtime_tolerance} (tier weight x{multiplier})")

        results.append(
            DestinationCandidate(
                node_id=node.id, node_name=node.name, blocked=blocked,
                blocking_reasons=sorted(set(blocking_reasons)), score=round(score, 1), reasons=reasons,
            )
        )

    results.sort(key=lambda c: (c.blocked, -c.score))
    return results


def rank_with_simulated_load(
    candidates: list[DestinationCandidate],
    nodes_by_id: dict,
    simulated_added_bytes: dict,
) -> list[DestinationCandidate]:
    """Re-rank recommend_destinations() output to account for load already
    committed to a destination earlier in the SAME batch, before its real
    telemetry (cpu_usage_pct/mem_usage_pct) catches up. Without this,
    evaluating every workload in a batch independently makes them all pick
    the same single emptiest node -- confirmed live 2026-09-11 (a
    balance-recommendation pass suggested 18 workloads move onto one node)
    and again 2026-09-12 (a node-maintenance evacuation plan sent all 5 VMs
    on the node to the same destination, which then ran out of memory
    headroom for the last one). Same 0.6-per-point balance weight
    recommend_destinations() itself uses, applied as a running penalty
    instead of from a fresh DB read each time.

    Caller is responsible for accumulating simulated_added_bytes[node_id] +=
    workload.memory_bytes after each pick.
    """
    def _adjusted(c: DestinationCandidate) -> float:
        node = nodes_by_id.get(c.node_id)
        added = simulated_added_bytes.get(c.node_id, 0)
        if not node or not node.mem_total_bytes or not added:
            return c.score
        return c.score - (added / node.mem_total_bytes) * 100 * 0.6

    return sorted(candidates, key=lambda c: (c.blocked, -_adjusted(c)))


def rescore_migrate_plan(db: Session, migrate_plan: list[dict]) -> list[dict]:
    """Re-run the destination scoring pass fresh against current live
    state for an already-built plan -- the "Re-score" button on an
    awaiting-approval batch plan. Any item the user has manually picked
    (item["manually_set"], set by the edit-destination endpoint) keeps
    its destination exactly as set; every OTHER item gets a freshly
    recomputed pick, with the manually-set ones' memory still counted
    toward the running cumulative load so the rest of the plan stays
    honest to what's actually committed -- manual edits stay overrides.

    Works uniformly across every batch type (node.enter_maintenance/
    maintenance.run/node.evacuate/cluster.rebalance) since they all share
    this exact migrate_plan item shape -- eligible destinations for a
    given item are simply every online, non-maintenance node in its own
    cluster other than wherever it's currently running, which is
    correct whether "current node" means the one specific node being
    evacuated (the first three) or wherever a workload happens to be
    right now (cluster.rebalance, which can span the whole cluster).
    """
    from .discovery import build_pve_client
    from .models import Cluster, PveTarget

    workloads_by_id = {
        item["workload_id"]: db.query(Workload).filter(Workload.id == item["workload_id"]).one_or_none()
        for item in migrate_plan
    }

    clients: dict = {}
    for cluster_id in {wl.cluster_id for wl in workloads_by_id.values() if wl is not None}:
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none() if cluster else None
        if target is None:
            clients[cluster_id] = None
            continue
        try:
            client, _cred = build_pve_client(db, target)
            clients[cluster_id] = client
        except Exception:
            clients[cluster_id] = None

    simulated_added_bytes: dict = {}
    updated: list[dict] = []

    with ExitStack() as stack:
        for client in clients.values():
            if client is not None:
                stack.enter_context(client)

        for item in migrate_plan:
            workload = workloads_by_id.get(item["workload_id"])
            if workload is None or workload.is_missing:
                updated.append(item)
                continue

            if item.get("manually_set"):
                dest_id = item.get("destination_node_id")
                if dest_id:
                    dest_uuid = dest_id if isinstance(dest_id, uuid.UUID) else uuid.UUID(str(dest_id))
                    note_planned_move(simulated_added_bytes, workload, dest_uuid)
                updated.append(item)
                continue

            if item.get("transport") == "shutdown_in_place":
                # Staying right here, powered off, until maintenance
                # completes -- not landing on any destination at all, so it
                # has no destination to recompute and shouldn't count
                # against any node's simulated headroom either -- the
                # shut-down-in-place option shouldn't get treated as if it
                # were still migrating somewhere.
                updated.append(item)
                continue

            candidates_all = (
                db.query(Node)
                .filter(
                    Node.cluster_id == workload.cluster_id, Node.id != workload.node_id,
                    Node.is_missing.is_(False), Node.status == "online", Node.maintenance_mode.is_(False),
                )
                .all()
            )
            nodes_by_id = {n.id: n for n in candidates_all}
            client = clients.get(workload.cluster_id)

            ranked = recommend_destinations(db, client, workload, candidates_all, simulated_added_bytes=simulated_added_bytes)
            ranked = rank_with_simulated_load(ranked, nodes_by_id, simulated_added_bytes)
            serialized_candidates = [
                {
                    "node_id": str(c.node_id), "node_name": c.node_name, "score": c.score,
                    "blocked": c.blocked, "blocking_reasons": c.blocking_reasons, "reasons": c.reasons,
                }
                for c in ranked
            ]
            top = next((c for c in ranked if not c.blocked), None)
            if top is None:
                updated.append({**item, "candidates": serialized_candidates})
                continue

            destination_node = nodes_by_id[top.node_id]
            note_planned_move(simulated_added_bytes, workload, top.node_id)

            storage_rec = recommend_storage_for_candidate(
                db, destination_node.id, bool(item.get("currently_on_shared")), storage_preference=item.get("storage_preference"),
            )
            updated.append({
                **item,
                "destination_node_id": str(destination_node.id),
                "destination_node": destination_node.name,
                "destination_storage_id": storage_rec["id"] if storage_rec else None,
                "candidates": serialized_candidates,
            })

    return updated


def is_currently_on_shared_storage(client, source_node: Node, workload: Workload, db: Session) -> bool:
    """True only if we can positively confirm the workload's disk(s) are NOT
    on node-local storage right now. Fails conservatively (False, i.e.
    "treat as needing a storage decision") if config can't be read --
    matches every other storage-locality check in this app."""
    if client is None:
        return False
    config = _qemu_config(client, source_node.name, workload)
    node_local_disks = workload_node_local_disk_storages(db, source_node, config)
    return node_local_disks == []


def current_storage_name(client, source_node: Node, workload: Workload) -> str | None:
    """The actual storage name this workload's disk(s) currently sit on,
    parsed from its live qemu config -- same underlying data
    is_currently_on_shared_storage() already reads, exposed here as a real
    name for display (a migrate-plan row previously said whether storage
    was "shared" or not, never which storage that actually was, even when
    keeping it unchanged). None if config can't be read or the workload
    has no disks. When multiple disks span different storages, returns the
    first alphabetically -- same convention as the current-storage API
    endpoint (/api/workloads/current-storage) already uses."""
    if client is None:
        return None
    config = _qemu_config(client, source_node.name, workload)
    if config is None:
        return None
    names = sorted(_workload_disk_storage_names(config))
    return names[0] if names else None


def recommend_storage_for_candidate(
    db: Session, candidate_node_id, currently_on_shared: bool, *, storage_preference: str | None = None,
) -> Optional[dict]:
    """The single source of truth for "what storage should this migration
    use" -- shared by both the /recommend API endpoint and any workflow
    module (evacuation, maintenance) that plans migrations without going
    through that endpoint. Keeping this in one place is what a prior bug
    here should have been from the start: two copies of this logic already
    diverged once (recommend_destinations()'s DestinationCandidate never
    carried a recommended_storage field, but callers assumed it did).

    `storage_preference` is the workload's own sticky setting ('local' |
    'shared' | None). None means infer from where its disk lives today --
    already-shared stays shared, already-local targets the destination's
    own local storage. This used to unconditionally prefer shared storage
    for anything not already there ("avoids re-locking it to another
    node's local disk") -- a real bug found in production use, the
    opposite of a deployment that deliberately keeps everything on local
    storage and only leans on shared storage where that's already the
    workload's home."""
    node_storages = db.query(Storage).filter(
        (Storage.scope == "cluster-shared") | ((Storage.scope == "node-local") & (Storage.node_id == candidate_node_id))
    ).all()
    shared = [s for s in node_storages if s.scope == "cluster-shared"]
    local = sorted(
        [s for s in node_storages if s.scope == "node-local" and s.capacity_bytes and s.used_bytes is not None],
        key=lambda s: (s.capacity_bytes - s.used_bytes), reverse=True,
    )
    preference = storage_preference if storage_preference in ("local", "shared") else ("shared" if currently_on_shared else "local")

    # A node-level default (Nodes/Maintenance page) pins WHICH storage
    # this node should use as its migration target, instead of always
    # auto-picking (the local pool with the most free space, or the
    # cluster's shared storage). It can point at EITHER a local pool or
    # the cluster-shared storage -- some deployments run local-SSD-first,
    # others run shared-first with local barely used, if at all. A pin
    # wins outright over the local/shared auto-inference below, since it's
    # a deliberate, standing choice for this node specifically -- the one
    # case this doesn't yet cover is a workload with its OWN explicit
    # preference pointed the other way from a node's pin; that's a real
    # edge case worth a rule if it ever actually comes up, not something
    # to guess a precedence for now. A stale policy pointing at a
    # since-removed storage falls through to the same auto-pick as if
    # nothing were set.
    default_storage_id = get_node_default_storage_id(db, candidate_node_id)
    pinned = next((s for s in node_storages if str(s.id) == str(default_storage_id)), None) if default_storage_id else None
    if pinned:
        if pinned.scope == "cluster-shared" and currently_on_shared:
            return None  # already on shared, pin agrees -- "keep current" is the real no-copy path
        return {"id": str(pinned.id), "name": pinned.name, "scope": pinned.scope,
                "reason": "pinned as this node's default storage"}

    if preference == "shared":
        if currently_on_shared and shared:
            return None  # already there -- "keep current" is the real no-copy path
        if shared:
            return {"id": str(shared[0].id), "name": shared[0].name, "scope": "cluster-shared",
                    "reason": "workload's storage preference is shared -- relocating there"}
        if local:
            return {"id": str(local[0].id), "name": local[0].name, "scope": "node-local",
                    "reason": "no cluster-shared storage reachable here -- falling back to the local pool with the most free space"}
        return None

    # preference == "local"
    if local:
        return {"id": str(local[0].id), "name": local[0].name, "scope": "node-local",
                "reason": "workload's storage preference is local -- the local pool with the most free space"}
    if shared:
        return {"id": str(shared[0].id), "name": shared[0].name, "scope": "cluster-shared",
                "reason": "destination node has no local storage -- falling back to cluster-shared"}
    return None
