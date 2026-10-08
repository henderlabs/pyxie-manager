"""Recommendation generation + lifecycle reconciliation.

Unlike findings (which fully auto-resolve), a recommendation's lifecycle is
partly user-driven: regenerating never silently reopens something the user
dismissed. Only 'open'/'acknowledged'/'snoozed'(expired) recommendations get
auto-resolved when the underlying condition disappears.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .capacity import compute_capacity
from .discovery import build_pve_client
from .models import Cluster, Node, PveTarget, Recommendation, Workload
from .placement import (
    note_planned_move,
    current_storage_name,
    get_cluster_storage_preference,
    is_currently_on_shared_storage,
    rank_with_simulated_load,
    recommend_destinations,
    recommend_storage_for_candidate,
)
from .rightsizing import assess_all_workloads, refresh_rightsizing_cache

TRIAGED_STATES = ("acknowledged", "dismissed", "snoozed")
SEVERITY_RANK = {"info": 0, "unknown": 0, "warning": 1, "critical": 2}


def triage_fingerprint(category: str, evidence: dict | None) -> str | None:
    """What the operator was looking at when they acknowledged or dismissed. A rightsizing suggestion is its suggested
    size; when that changes, the earlier decision no longer applies. Other categories have no fingerprint, so their
    triage ends only when they resolve and come back, or get more severe."""
    if category != "rightsizing" or not evidence:
        return None
    cpu = (evidence.get("cpu_suggestion") or {}).get("suggested")
    mem = (evidence.get("memory_suggestion") or {}).get("suggested_bytes")
    return f"cpu={cpu};mem={mem}"


def clear_triage(r: Recommendation) -> None:
    r.acknowledged_at = r.acknowledged_by = r.dismissed_at = r.dismissed_by = r.triage_fingerprint = None
    r.snoozed_until = None


def apply_triage(r: Recommendation, action: str, who: str, now: datetime) -> None:
    """action: acknowledge | dismiss | reopen."""
    clear_triage(r)
    if action == "reopen":
        r.lifecycle_state = "open"
        return
    r.lifecycle_state = "acknowledged" if action == "acknowledge" else "dismissed"
    if action == "acknowledge":
        r.acknowledged_at, r.acknowledged_by = now, who
    else:
        r.dismissed_at, r.dismissed_by = now, who
    r.triage_fingerprint = triage_fingerprint(r.category, r.evidence)


def _triage_ended(existing: Recommendation, rec: dict) -> bool:
    """The suggestion changed (fingerprint) or got more severe since the operator looked."""
    fp = existing.triage_fingerprint
    if fp is not None and triage_fingerprint(rec["category"], rec.get("evidence")) != fp:
        return True
    return SEVERITY_RANK.get(rec.get("severity", "info"), 0) > SEVERITY_RANK.get(existing.severity, 0)

# How much better a candidate node's score has to be before it's worth
# proactively suggesting a move -- roughly one performance/trust tier step,
# or a real (not marginal) difference in memory headroom. Below this,
# ranking noise between two nearly-equal nodes would otherwise churn out a
# new "move it" suggestion practically every cycle.
PLACEMENT_IMPROVEMENT_THRESHOLD = 15.0


def _upsert(db: Session, rec: dict):
    now = datetime.now(timezone.utc)
    existing = db.query(Recommendation).filter(Recommendation.dedupe_key == rec["dedupe_key"]).one_or_none()
    if existing is None:
        db.add(
            Recommendation(
                object_type=rec.get("object_type"),
                object_id=rec.get("object_id"),
                category=rec["category"],
                title=rec["title"],
                evidence=rec.get("evidence"),
                expected_benefit=rec.get("expected_benefit"),
                possible_impact=rec.get("possible_impact"),
                severity=rec.get("severity", "info"),
                risk=rec.get("risk", "low"),
                confidence=rec.get("confidence"),
                observation_window_days=rec.get("observation_window_days"),
                dedupe_key=rec["dedupe_key"],
                generated_at=now,
                lifecycle_state="open",
            )
        )
        return "created"

    if existing.lifecycle_state in TRIAGED_STATES and _triage_ended(existing, rec):
        existing.lifecycle_state = "open"
        clear_triage(existing)

    if existing.lifecycle_state in ("dismissed", "snoozed"):
        # respect the user's decision; just refresh evidence for reference
        existing.evidence = rec.get("evidence")
        existing.generated_at = now
        return "left_dismissed"

    existing.title = rec["title"]
    existing.evidence = rec.get("evidence")
    existing.expected_benefit = rec.get("expected_benefit")
    existing.possible_impact = rec.get("possible_impact")
    existing.severity = rec.get("severity", "info")
    existing.confidence = rec.get("confidence")
    existing.observation_window_days = rec.get("observation_window_days")
    existing.generated_at = now
    if existing.lifecycle_state == "resolved":
        existing.lifecycle_state = "open"  # it came back: a new occurrence, earlier triage does not carry over
        existing.resolved_at = None
        clear_triage(existing)
    return "updated"


def generate_recommendations(db: Session) -> dict:
    current: list[dict] = []
    # Computed once and shared: _capacity_recommendations already needed
    # this for its own "cluster is at 90%+ allocation" check, and
    # _rightsizing_recommendations now needs the same most_constrained_resource
    # verdict per cluster to decide which rightsizing findings to prioritize --
    # no reason to run compute_capacity()'s per-workload observation queries
    # twice in the same pass.
    capacity_reports = compute_capacity(db)
    # Computed once and cached (refresh_rightsizing_cache persists it as
    # what GET /rightsizing now serves) -- this used to be a second,
    # redundant assess_all_workloads() call on top of the one GET
    # /rightsizing did live on every page load; now there's exactly one
    # computation per cycle, shared by both.
    assessments = assess_all_workloads(db)
    refresh_rightsizing_cache(db, assessments)
    current.extend(_rightsizing_recommendations(assessments, capacity_reports))
    current.extend(_placement_recommendations(db))
    current.extend(_capacity_recommendations(db, capacity_reports))
    current.extend(_update_recommendations(db))

    seen_keys = {r["dedupe_key"] for r in current}
    counts = {"created": 0, "updated": 0, "left_dismissed": 0}
    for rec in current:
        outcome = _upsert(db, rec)
        counts[outcome] = counts.get(outcome, 0) + 1

    now = datetime.now(timezone.utc)
    resolvable = (
        db.query(Recommendation)
        .filter(Recommendation.lifecycle_state.in_(["open", *TRIAGED_STATES]))
    )
    if seen_keys:
        resolvable = resolvable.filter(~Recommendation.dedupe_key.in_(seen_keys))
    to_resolve = resolvable.all()
    for r in to_resolve:
        r.lifecycle_state = "resolved"
        r.resolved_at = now
        clear_triage(r)

    db.commit()
    counts["resolved"] = len(to_resolve)
    return counts


def _rightsizing_recommendations(assessments: list[dict], capacity_reports: list[dict]) -> list[dict]:
    """One recommendation per workload, not one per resource dimension --
    a workload with both a CPU and a memory suggestion gets a single card
    covering both, matching how the Recommendations page's Apply action
    already treats them as one combined change (shutting a workload down
    twice, once per dimension, never made sense).

    Severity escalates in two independent ways: an
    'increase' suggestion (the workload is already too tight, not
    over-allocated) always outranks a plain 'decrease' one, and either kind
    gets bumped a tier further when it touches whatever resource is
    cluster_capacity()'s most_constrained_resource for this workload's
    cluster right now -- "recommendations should always favor the most
    restrained resource of the host/cluster." A decrease on the scarce
    resource matters more to surface (freeing it helps the whole cluster);
    an increase on the scarce resource is the worst case (a workload
    starved on exactly what the cluster overall is short on).
    """
    most_constrained_by_cluster = {r["cluster_id"]: r["most_constrained_resource"] for r in capacity_reports}

    recs = []
    for assessment in assessments:
        cpu_s = assessment["cpu_suggestion"]
        mem_s = assessment["memory_suggestion"]
        if not cpu_s and not mem_s:
            continue

        wl_label = assessment["name"] or f"VMID {assessment['vmid']}"
        title_parts = []
        if cpu_s:
            title_parts.append(f"{cpu_s['current']} → {cpu_s['suggested']} vCPU")
        if mem_s:
            current_gb = round(mem_s["current_bytes"] / (1024**3), 1)
            suggested_gb = round(mem_s["suggested_bytes"] / (1024**3), 1)
            title_parts.append(f"{current_gb}GB → {suggested_gb}GB RAM")

        resources_flagged = {}
        if cpu_s:
            resources_flagged["cpu"] = cpu_s["direction"]
        if mem_s:
            resources_flagged["memory"] = mem_s["direction"]
        has_increase = "increase" in resources_flagged.values()

        constrained_resource = most_constrained_by_cluster.get(assessment["cluster_id"])
        touches_constrained_resource = constrained_resource in resources_flagged

        if has_increase:
            severity = "critical" if touches_constrained_resource else "warning"
            expected_benefit = (
                "Reduces the risk of memory pressure (swapping/OOM) or CPU scheduling contention for this "
                "workload by matching its allocation to observed usage."
            )
            possible_impact = "Uses more of the node's/cluster's allocatable capacity."
        else:
            severity = "warning" if touches_constrained_resource else "info"
            expected_benefit = "Frees CPU/memory allocation for other workloads without observed performance impact."
            possible_impact = "Reduced burst headroom if usage pattern changes."

        recs.append(
            {
                "dedupe_key": f"rightsizing:{assessment['workload_id']}",
                "object_type": "workload",
                "object_id": assessment["workload_id"],
                "category": "rightsizing",
                "title": f"{wl_label}: consider {', '.join(title_parts)}",
                "evidence": {
                    "cpu": assessment["cpu"],
                    "memory": assessment["memory"],
                    "cpu_suggestion": cpu_s,
                    "memory_suggestion": mem_s,
                    "most_constrained_resource": constrained_resource,
                },
                "expected_benefit": expected_benefit,
                "possible_impact": possible_impact,
                "severity": severity,
                "risk": "low",
                "confidence": assessment["confidence"],
                "observation_window_days": round(assessment["observation_days"]),
            }
        )
    return recs


def _placement_recommendations(
    db: Session, source_node_ids: set | None = None, blocked_out: list | None = None, *,
    cluster_ids: set | None = None, metric: str | None = None, min_improvement: float | None = None,
    max_moves: int | None = None, skip_workload_ids: set | None = None,
) -> list[dict]:
    """DRS-style proactive load-balancing: for each running VM, re-run the
    exact same scoring engine the interactive migration form's 'recommend'
    auto-fill already uses (placement.py), including THIS workload's own
    current node as one of the candidates -- if something else scores
    meaningfully higher right now, suggest moving it. Purely live-state
    based (not historical like rightsizing), so there's no stale-data
    equivalent to guard against; PLACEMENT_IMPROVEMENT_THRESHOLD is what
    keeps this from churning on two nodes that are nearly tied.

    One real subtlety: recommend_destinations() only ever sees the DB's
    actual current state, so evaluating every workload independently makes
    them ALL pick the same single emptiest node -- confirmed live against
    the real cluster, it suggested moving 18 different workloads onto one
    node at once, which is obviously not "balance." Fixed by simulating,
    within this one evaluation pass, the memory a node would carry if
    earlier (larger, so processed first) workloads' suggestions were
    followed -- same balance-scoring weight (0.6 per point of headroom %)
    placement.py itself uses, just applied as a running penalty instead of
    from a fresh DB read each time. This makes suggestions within a single
    pass spread out instead of piling onto one node; applying them still
    happens one at a time, same Safety Contract as any other migration.

    source_node_ids, when given, restricts which workloads are evaluated as
    move CANDIDATES (their current node must be in the set) -- destinations
    are never restricted to this set, any eligible online node in the
    cluster is still fair game. This is what the Maintenance page's
    on-demand "Balance Load" trigger uses when one or more nodes are
    selected -- balance the VMs ON the selected nodes, without confining
    where they can go. None (the periodic worker job's own call) means
    "evaluate every running VM cluster-wide", unchanged.
    """
    recs: list[dict] = []
    from .balance_config import get_config, resolve_metric

    threshold = PLACEMENT_IMPROVEMENT_THRESHOLD if min_improvement is None else min_improvement
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all():
        if cluster_ids is not None and cluster.id not in cluster_ids:
            continue
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        if target is None:
            continue
        nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).all()
        if len(nodes) < 2:
            continue
        cluster_metric = resolve_metric(metric or get_config(db, cluster.id)["metric"], nodes)
        cluster_recs_start = len(recs)
        try:
            client, _cred = build_pve_client(db, target)
        except Exception:
            continue

        node_by_id = {n.id: n for n in nodes}
        simulated_added_bytes = {n.id: 0 for n in nodes}

        with client:
            workloads = (
                db.query(Workload)
                .filter(
                    Workload.cluster_id == cluster.id, Workload.is_missing.is_(False),
                    Workload.status == "running", Workload.type == "vm",
                )
                .order_by(Workload.memory_bytes.desc().nullslast())  # biggest movers first -- they matter most to real balance
                .all()
            )
            for wl in workloads:
                if max_moves is not None and len(recs) - cluster_recs_start >= max_moves:
                    break
                if source_node_ids is not None and wl.node_id not in source_node_ids:
                    continue
                if wl.do_not_move or (skip_workload_ids and wl.id in skip_workload_ids):
                    continue
                # Exclude offline/maintenance-mode nodes as destinations --
                # always keep the workload's own current node in the set so
                # `current` below still resolves, even though a maintenance-
                # mode current node can't happen in practice (it would have
                # been evacuated already). recommend_destinations() has no
                # online/maintenance check of its own; every caller is
                # responsible for filtering its own candidate list (2026-09-11:
                # this call site was passing every node, unfiltered, straight
                # through -- found live when a maintenance-mode node still
                # showed up as a suggested destination on the Dashboard).
                candidate_nodes = [n for n in nodes if n.id == wl.node_id or (n.status == "online" and not n.maintenance_mode)]
                try:
                    candidates = recommend_destinations(db, client, wl, candidate_nodes, simulated_added_bytes=simulated_added_bytes, metric=cluster_metric)
                except Exception:
                    continue
                if not candidates:
                    continue

                def _adjusted_score(c):
                    node = node_by_id.get(c.node_id)
                    added = simulated_added_bytes.get(c.node_id, 0)
                    if not node or not node.mem_total_bytes or not added:
                        return c.score
                    return c.score - (added / node.mem_total_bytes) * 100 * 0.6

                # Same running-penalty logic as rank_with_simulated_load()
                # (shared with the maintenance/evacuation plan-builders) --
                # kept as a local closure here too since _adjusted_score is
                # also needed standalone below for the improvement delta.
                ranked = rank_with_simulated_load(candidates, node_by_id, simulated_added_bytes)
                current = next((c for c in ranked if c.node_id == wl.node_id), None)
                best = ranked[0]
                if current is None:
                    continue
                moving = not best.blocked and best.node_id != wl.node_id
                improvement = _adjusted_score(best) - _adjusted_score(current)
                moving = moving and improvement >= threshold
                if blocked_out is not None:
                    # Balance Load preview: a destination that would have been a real
                    # improvement but a hard rule (keep-apart, headroom, ...) refused it,
                    # so the operator sees why a guest is not moving where it looks best.
                    alt = max(
                        (c for c in ranked if c.blocked and c.node_id != wl.node_id),
                        key=lambda c: c.score, default=None,
                    )
                    if alt is not None:
                        alt_gain = alt.score - current.score
                        if alt_gain >= threshold and (not moving or alt.score > best.score):
                            blocked_out.append({
                                "workload_id": str(wl.id), "vmid": wl.vmid, "name": wl.name,
                                "memory_bytes": wl.memory_bytes,
                                "source_node_id": str(wl.node_id), "source_node": current.node_name,
                                "blocked_node_id": str(alt.node_id), "blocked_node": alt.node_name,
                                "blocking_reasons": alt.blocking_reasons,
                                "improvement": round(alt_gain, 1),
                                "planned_instead": best.node_name if moving else None,
                            })
                if not moving:
                    continue

                note_planned_move(simulated_added_bytes, wl, best.node_id)

                # Storage decision, same source of truth every other
                # migration path (evacuation, maintenance, the manual "Move
                # to Another Node" action) already uses -- Balance Load was
                # the one path that never called this, so a move could
                # silently leave a workload on storage that contradicts its
                # own preference. A real gap found live: two workloads with
                # a local-storage preference landed on shared storage
                # anyway after a Balance Load move -- a VM on local storage
                # migrating to a node without matching local storage HAS to
                # change storage regardless, so this needs to honor the
                # VM's set preference either way.
                effective_pref = wl.storage_preference or get_cluster_storage_preference(db, cluster.id)
                try:
                    currently_on_shared = is_currently_on_shared_storage(client, node_by_id.get(wl.node_id), wl, db)
                    current_storage = current_storage_name(client, node_by_id.get(wl.node_id), wl)
                except Exception:
                    currently_on_shared = False
                    current_storage = None
                storage_rec = recommend_storage_for_candidate(
                    db, best.node_id, currently_on_shared, storage_preference=effective_pref
                )

                wl_label = wl.name or f"VMID {wl.vmid}"
                possible_impact = "Live migration -- typically no downtime, brief network blip possible."
                if storage_rec:
                    possible_impact += (
                        f" Also relocates storage to {storage_rec['name']} to match this workload's storage "
                        "preference -- disk-transfer-bound, takes noticeably longer than a compute-only move."
                    )
                recs.append(
                    {
                        "dedupe_key": f"placement:{wl.id}",
                        "object_type": "workload",
                        "object_id": wl.id,
                        "category": "placement",
                        "title": f"{wl_label}: consider moving {current.node_name} → {best.node_name}",
                        "evidence": {
                            "current_node": current.node_name, "current_score": current.score,
                            "suggested_node": best.node_name, "suggested_node_id": str(best.node_id), "suggested_score": best.score,
                            "improvement": round(improvement, 1), "reasons": best.reasons,
                            "suggested_storage": storage_rec,
                            # Everything below is unused by this function's own
                            # passive-Recommendations-card callers -- carried
                            # so cluster.rebalance (balance_workflow.py) can
                            # reuse this exact scoring pass as its migrate_plan
                            # source instead of re-implementing it, and so its
                            # plan items support the same editable-destination
                            # dropdown every other batch migration plan does.
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
                        },
                        "expected_benefit": f"Better cluster balance -- {best.node_name} scores {round(improvement, 1)} points higher than staying on {current.node_name}.",
                        "possible_impact": possible_impact,
                        "severity": "info",
                        "risk": "low",
                        "confidence": "high",
                    }
                )
    return recs


def _capacity_recommendations(db: Session, capacity_reports: list[dict] | None = None) -> list[dict]:
    recs = []
    for cluster_report in (capacity_reports if capacity_reports is not None else compute_capacity(db)):
        if cluster_report["memory_allocation_pct"] and cluster_report["memory_allocation_pct"] > 90:
            recs.append(
                {
                    "dedupe_key": f"capacity.memory_pressure:{cluster_report['cluster_id']}",
                    "object_type": "cluster",
                    "object_id": cluster_report["cluster_id"],
                    "category": "capacity",
                    "title": f"Cluster '{cluster_report['name']}' memory allocation is at {cluster_report['memory_allocation_pct']}%",
                    "evidence": {"most_constrained_resource": cluster_report["most_constrained_resource"]},
                    "expected_benefit": "Avoids allocation failures for new workloads.",
                    "possible_impact": "None -- informational.",
                    "severity": "warning",
                    "risk": "low",
                    "confidence": "high",
                }
            )
    return recs


def _update_recommendations(db: Session) -> list[dict]:
    recs = []
    for n in db.query(Node).filter(Node.is_missing.is_(False), Node.pending_updates.isnot(None)).all():
        if n.pending_updates and n.pending_updates > 0:
            recs.append(
                {
                    "dedupe_key": f"updates.pending:{n.id}",
                    "object_type": "node",
                    "object_id": n.id,
                    "category": "updates",
                    "title": f"Node '{n.name}' has {n.pending_updates} package update(s) pending",
                    "evidence": {"pending_updates": n.pending_updates},
                    "expected_benefit": "Keeps node current with security/bugfix patches.",
                    "possible_impact": "A reboot may be required after applying updates.",
                    "severity": "info",
                    "risk": "low",
                    "confidence": "high",
                }
            )
    return recs
