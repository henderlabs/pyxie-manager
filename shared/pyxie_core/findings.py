"""Formal findings engine. A finding is an observed condition, not
necessarily a recommendation (recommendations.py builds on top of these).

Findings are reconciled every evaluation pass by a stable dedupe_key: seen
again -> last_observed bumped, active=True; not seen this pass -> the
existing active finding is auto-resolved (active=False, resolved_at=now).
Nothing is ever hard-deleted, so history survives.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import AppSettings, Cluster, Finding, Node, Notification, PlacementAffinityRule, Policy, Provider, PveTarget, PveTask, Storage, Workload, WorkloadLiveness
from .inventory_health import stale_minutes
from .notifications import dispatch_event
from .vm_liveness import finding_worthy

TASK_LOOKBACK_DAYS = 7
DEFAULT_STORAGE_WARNING_PCT = 85


def _storage_warning_threshold(db: Session) -> float:
    policy = (
        db.query(Policy)
        .filter(Policy.key == "storage.warning_threshold_pct", Policy.scope_type == "organization")
        .one_or_none()
    )
    if policy and isinstance(policy.value, dict) and "pct" in policy.value:
        return float(policy.value["pct"])
    return DEFAULT_STORAGE_WARNING_PCT


def _notify(db: Session, events: list, f: Finding, *, recovered: bool = False) -> None:
    """Record an in-app Notification now (same transaction as the finding)
    and queue the event for email dispatch after the commit. Who actually
    gets emailed is decided by the notification rules, not here."""
    db.add(
        Notification(
            severity="informational" if recovered else f.severity,
            title=f"Recovered: {f.title}" if recovered else f.title,
            message=f"category: {f.category}",
            object_type=f.object_type,
            object_id=f.object_id,
            status="unread",
            source="findings",
        )
    )
    events.append({"severity": f.severity, "category": f.category, "title": f.title, "recovered": recovered})


def _hold_down(db: Session) -> timedelta:
    minutes = db.query(AppSettings.notification_hold_down_minutes).filter(AppSettings.id == 1).scalar()
    return timedelta(minutes=5 if minutes is None else minutes)


def _settle_notifications(db: Session, now: datetime, events: list) -> None:
    """Announce a finding's state change (started / cleared) only once that
    state has held for the hold-down period, so a flapping condition -- a
    node dropping in and out of quorum every minute -- yields one alert
    when it settles, not an alert and a recovery per flip. Flaps shorter
    than the hold-down are never announced at all."""
    hold = _hold_down(db)
    candidates = (
        db.query(Finding)
        .filter(Finding.severity.in_(("warning", "critical")))
        .filter(
            (Finding.active.is_(True) & (Finding.notified_active.isnot(True)))
            | (Finding.active.is_(False) & (Finding.notified_active.is_(True)))
        )
        .all()
    )
    for f in candidates:
        since = f.state_since
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        if since is not None and now - since < hold:
            continue
        _notify(db, events, f, recovered=not f.active)
        f.notified_active = f.active


SEVERITY_RANK = {"info": 0, "unknown": 0, "warning": 1, "critical": 2}


def triage_state(f: Finding) -> str:
    """open | acknowledged | dismissed (dismissed wins if both were somehow set)."""
    if f.dismissed_at is not None:
        return "dismissed"
    if f.acknowledged_at is not None:
        return "acknowledged"
    return "open"


def clear_triage(f: Finding) -> None:
    f.acknowledged_at = f.acknowledged_by = f.dismissed_at = f.dismissed_by = None


def _reconcile(db: Session, current: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    seen_keys = set()
    events: list[dict] = []

    for f in current:
        seen_keys.add(f["dedupe_key"])
        existing = db.query(Finding).filter(Finding.dedupe_key == f["dedupe_key"]).one_or_none()
        if existing is None:
            db.add(
                Finding(
                    object_type=f.get("object_type"),
                    object_id=f.get("object_id"),
                    category=f["category"],
                    severity=f["severity"],
                    title=f["title"],
                    evidence=f.get("evidence"),
                    dedupe_key=f["dedupe_key"],
                    first_observed=now,
                    last_observed=now,
                    active=True,
                    source=f.get("source", "system"),
                    confidence=f.get("confidence"),
                    state_since=now,
                    notified_active=None,
                )
            )
        else:
            if not existing.active:
                existing.state_since = now  # a new occurrence begins
                clear_triage(existing)  # acknowledged/dismissed applied to the old occurrence
            elif SEVERITY_RANK.get(f["severity"], 0) > SEVERITY_RANK.get(existing.severity, 0):
                clear_triage(existing)  # it got worse since the operator looked
            existing.last_observed = now
            existing.active = True
            existing.resolved_at = None
            existing.severity = f["severity"]
            existing.title = f["title"]
            existing.evidence = f.get("evidence")

    active_query = db.query(Finding).filter(Finding.active.is_(True))
    if seen_keys:
        active_query = active_query.filter(~Finding.dedupe_key.in_(seen_keys))
    resolved = active_query.all()
    for r in resolved:
        r.active = False
        r.resolved_at = now
        r.state_since = now

    db.flush()
    # A notification is created when a state change has SETTLED, never on
    # every observation -- see _settle_notifications.
    _settle_notifications(db, now, events)
    db.commit()

    # After commit, so a slow/broken mail server can't affect the findings
    # write. dispatch_event never raises.
    for e in events:
        dispatch_event(db, observed_at=now, **e)

    return {"observed": len(current), "resolved": len(resolved)}


def evaluate_findings(db: Session) -> dict:
    current: list[dict] = []
    storage_warning_pct = _storage_warning_threshold(db)

    clusters = db.query(Cluster).filter(Cluster.is_missing.is_(False)).all()
    for c in clusters:
        if c.quorate is False:
            current.append(
                {
                    "dedupe_key": f"cluster.not_quorate:{c.id}",
                    "object_type": "cluster",
                    "object_id": c.id,
                    "category": "quorum",
                    "severity": "critical",
                    "title": f"Cluster '{c.name}' is not quorate",
                    "evidence": {"cluster": c.name},
                }
            )

    for t in db.query(PveTarget).all():
        provider = db.query(Provider).filter(Provider.id == t.provider_id).one_or_none()
        health = provider.connection_health if provider else "unknown"
        status = t.endpoint_status or {}
        unhealthy = status.get("unhealthy") or []
        if health in ("unavailable", "tls_error", "authentication_failed", "connection_failed"):
            current.append(
                {
                    "dedupe_key": f"pve.target_unreachable:{t.id}",
                    "object_type": "pve_target",
                    "object_id": t.id,
                    "category": "connectivity",
                    "severity": "critical",
                    "title": f"PyXie cannot connect to Proxmox target '{t.name}' ({health.replace('_', ' ')})",
                    "evidence": {"target": t.name, "health": health, "error": provider.last_error, "unhealthy": unhealthy},
                }
            )
        elif unhealthy and status.get("active"):
            current.append(
                {
                    "dedupe_key": f"pve.endpoint_failover:{t.id}",
                    "object_type": "pve_target",
                    "object_id": t.id,
                    "category": "connectivity",
                    "severity": "warning",
                    "title": f"Proxmox target '{t.name}': {len(unhealthy)} cluster member(s) unreachable, PyXie is connected via {status['active']}",
                    "evidence": {"target": t.name, "active": status["active"], "unhealthy": unhealthy},
                }
            )

    nodes = db.query(Node).filter(Node.is_missing.is_(False)).all()
    versions_by_cluster: dict = {}
    for n in nodes:
        if n.status != "online":
            current.append(
                {
                    "dedupe_key": f"node.offline:{n.id}",
                    "object_type": "node",
                    "object_id": n.id,
                    "category": "node",
                    "severity": "critical",
                    "title": f"Node '{n.name}' is offline",
                    "evidence": {"node": n.name, "status": n.status},
                }
            )
        if n.pending_updates:
            current.append(
                {
                    "dedupe_key": f"node.pending_updates:{n.id}",
                    "object_type": "node",
                    "object_id": n.id,
                    "category": "update",
                    "severity": "warning",
                    "title": f"Node '{n.name}' has {n.pending_updates} pending update(s)",
                    "evidence": {"node": n.name, "pending_updates": n.pending_updates},
                }
            )
        if n.pve_version:
            versions_by_cluster.setdefault(n.cluster_id, set()).add(n.pve_version)

        if n.mem_total_bytes:
            allocated = sum(
                (w.memory_bytes or 0)
                for w in db.query(Workload).filter(Workload.node_id == n.id, Workload.is_missing.is_(False)).all()
            )
            if allocated > n.mem_total_bytes:
                overcommit_bytes = allocated - n.mem_total_bytes
                overcommit_pct = round(100 * overcommit_bytes / n.mem_total_bytes, 1)
                current.append(
                    {
                        "dedupe_key": f"node.memory_overcommit:{n.id}",
                        "object_type": "node",
                        "object_id": n.id,
                        "category": "capacity",
                        "severity": "warning",
                        "title": f"Node '{n.name}' has allocated memory {overcommit_pct}% over physical RAM",
                        "evidence": {
                            "node": n.name,
                            "allocated_bytes": allocated,
                            "physical_bytes": n.mem_total_bytes,
                            "overcommit_bytes": overcommit_bytes,
                        },
                    }
                )

    for cluster_id, versions in versions_by_cluster.items():
        if len(versions) > 1:
            current.append(
                {
                    "dedupe_key": f"cluster.version_mismatch:{cluster_id}",
                    "object_type": "cluster",
                    "object_id": cluster_id,
                    "category": "version",
                    "severity": "warning",
                    "title": f"Inconsistent PVE versions across nodes: {', '.join(sorted(versions))}",
                    "evidence": {"versions": sorted(versions)},
                }
            )

    for s in db.query(Storage).filter(Storage.is_missing.is_(False)).all():
        if s.status != "available":
            current.append(
                {
                    "dedupe_key": f"storage.unavailable:{s.id}",
                    "object_type": "storage",
                    "object_id": s.id,
                    "category": "storage",
                    "severity": "critical",
                    "title": f"Storage '{s.name}' is unavailable",
                    "evidence": {"storage": s.name, "scope": s.scope},
                }
            )
        elif s.capacity_bytes and s.used_bytes and s.used_bytes / s.capacity_bytes * 100 > storage_warning_pct:
            pct = round(s.used_bytes / s.capacity_bytes * 100, 1)
            current.append(
                {
                    "dedupe_key": f"storage.high_utilization:{s.id}",
                    "object_type": "storage",
                    "object_id": s.id,
                    "category": "storage",
                    "severity": "warning",
                    "title": f"Storage '{s.name}' is at {pct}% utilization",
                    "evidence": {"storage": s.name, "used_pct": pct},
                }
            )

    # Only the MOST RECENT task per (node, task_type) reflects current
    # state -- e.g. a recurring nightly vzdump job that failed for a week
    # straight and then got fixed should stop flagging the moment the
    # latest run succeeds, not linger as N separate historical findings
    # until each individually ages out of the lookback window. The
    # dedupe_key is keyed on (node, task_type), not the task's own upid, so
    # a newly-succeeding run naturally drops out of `current` next pass and
    # _reconcile() auto-resolves the old finding, same as everything else.
    cutoff = datetime.now(timezone.utc) - timedelta(days=TASK_LOOKBACK_DAYS)
    recent_tasks = (
        db.query(PveTask)
        .filter(PveTask.exit_status.isnot(None), PveTask.last_seen >= cutoff)
        .order_by(PveTask.started_at.desc())
        .all()
    )
    latest_by_group: dict[tuple, PveTask] = {}
    for t in recent_tasks:
        key = (t.node_id, t.task_type)
        if key not in latest_by_group:  # first hit per group is the latest, thanks to the ORDER BY above
            latest_by_group[key] = t

    for (node_id, task_type), t in latest_by_group.items():
        if t.exit_status == "OK":
            continue
        current.append(
            {
                "dedupe_key": f"task.failed:{node_id}:{task_type}",
                "object_type": "task",
                "object_id": t.id,
                "category": "task",
                "severity": "warning",
                "title": f"Latest {t.task_type or 'unknown'} task failed: {t.exit_status}",
                "evidence": {"upid": t.upid, "user": t.user, "exit_status": t.exit_status, "started_at": t.started_at.isoformat() if t.started_at else None},
            }
        )

    current.extend(_protection_findings(db))
    current.extend(_affinity_violation_findings(db))
    current.extend(_liveness_findings(db))
    current.extend(_wrapper_findings(db))
    current.extend(_stale_inventory_findings(db))

    return _reconcile(db, current)


def _stale_inventory_findings(db: Session) -> list[dict]:
    """PyXie's picture of the cluster has stopped refreshing. Everything it shows (VM states, plans for maintenance) is
    then old, and a maintenance plan built from it can be wrong."""
    from sqlalchemy import func

    last = db.query(func.max(Node.last_seen)).filter(Node.is_missing.is_(False)).scalar()
    settings = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
    mins = stale_minutes(last, datetime.now(timezone.utc), settings.inventory_refresh_interval_seconds if settings else 300)
    target = db.query(PveTarget).first()
    if mins is None or target is None:
        return []
    return [
        {
            "dedupe_key": "inventory.stale",
            "object_type": "pve_target",
            "object_id": target.id,
            "category": "connectivity",
            "severity": "warning",
            "title": f"Inventory has not refreshed for {mins} minutes",
            "evidence": {
                "minutes": mins, "last_refresh": last.isoformat() if last else None,
                "fix": "PyXie is showing old VM and host states, so maintenance plans built from them can be wrong. "
                       "Check the worker log for discovery messages; restarting the API and worker containers clears a stuck discovery lock.",
            },
        }
    ]


def _wrapper_findings(db: Session) -> list[dict]:
    """A node whose host-maintenance wrapper is older than the one this PyXie ships. Updates still work through the
    old wrapper, but newer features (live host output) need the new one. Low-key: info severity."""
    from . import host_kit
    from pathlib import Path

    kit_dir = Path(__file__).resolve().parents[1] / "host_maintenance_kit"
    if not (kit_dir / "pyxie-maint").exists():
        return []
    expected = host_kit.wrapper_version(kit_dir)
    out: list[dict] = []
    for node in db.query(Node).filter(Node.is_missing.is_(False), Node.wrapper_version.isnot(None)).all():
        if not host_kit.is_outdated(node.wrapper_version, expected):
            continue
        out.append(
            {
                "dedupe_key": f"host.wrapper_outdated:{node.id}",
                "object_type": "node",
                "object_id": node.id,
                "category": "version",
                "severity": "info",
                "title": f"Host wrapper on {node.name} is outdated ({node.wrapper_version}, current {expected})",
                "evidence": {
                    "node": node.name, "installed": node.wrapper_version, "expected": expected,
                    "fix": "Integrations > Prepare a host: generate the host script and run it as root on this node. "
                           "Updates still work meanwhile; live host output needs the new wrapper.",
                },
            }
        )
    return out


def _liveness_findings(db: Session) -> list[dict]:
    """A running VM whose QEMU has stopped answering. PVE still calls it `running`, but a live
    migration of it hangs (VM 115 sat dead ~2 days). Raised only after two bad probes in a row and
    only while the result is fresh, so a stopped or power-cycled VM clears on its own."""
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    rows = (
        db.query(WorkloadLiveness, Workload, Node)
        .join(Workload, Workload.id == WorkloadLiveness.workload_id)
        .join(Node, Node.id == Workload.node_id)
        .filter(Workload.is_missing.is_(False), WorkloadLiveness.consecutive_bad >= 2)
        .all()
    )
    for r, wl, node in rows:
        row = {"checked_at": r.checked_at, "consecutive_bad": r.consecutive_bad}
        if not finding_worthy(row, now):
            continue
        out.append(
            {
                "dedupe_key": f"vm.not_responding:{wl.id}",
                "object_type": "workload",
                "object_id": wl.id,
                "category": "liveness",
                "severity": "critical",
                "title": f"VM '{wl.name or wl.vmid}' ({wl.vmid}) on {node.name} is not responding",
                "evidence": {
                    "vmid": wl.vmid, "name": wl.name, "node": node.name, "state": r.state, "detail": r.detail,
                    "bad_since": r.bad_since.isoformat() if r.bad_since else None, "workload_ids": [str(wl.id)],
                    "fix": "PVE still shows it as running, but live migration and shutdown through PVE will hang. Power-cycle it from PVE.",
                },
            }
        )
    return out


def _affinity_violation_findings(db: Session) -> list[dict]:
    """Placement recommendations only evaluate affinity rules at
    dry-run/recommend time -- nothing re-checks a workload that's just
    sitting still, e.g. if it was moved by hand directly in PVE, outside
    PyXie entirely. This is that missing periodic check, run every
    findings-evaluation pass (same cadence as discovery) rather than as a
    separate schedule."""
    findings = []
    all_workloads = db.query(Workload).filter(Workload.is_missing.is_(False)).all()

    for rule in db.query(PlacementAffinityRule).all():
        if rule.scope_type == "workload_pair":
            wl_ids = {str(w) for w in (rule.workload_ids or [])}
            linked = [w for w in all_workloads if str(w.id) in wl_ids]
        else:  # tag_group
            linked = [w for w in all_workloads if w.tags and rule.tag in w.tags]

        if len(linked) < 2:
            continue

        by_node: dict = {}
        for w in linked:
            by_node.setdefault(w.node_id, []).append(w)

        if rule.rule_type == "keep_apart":
            for node_id, group in by_node.items():
                if len(group) < 2:
                    continue
                node = db.query(Node).filter(Node.id == node_id).one_or_none()
                names = [w.name or str(w.vmid) for w in group]
                findings.append(
                    {
                        "dedupe_key": f"affinity.keep_apart_violated:{rule.id}:{node_id}",
                        "object_type": "node",
                        "object_id": node_id,
                        "category": "placement",
                        "severity": "warning",
                        "title": f"Affinity rule violated: {', '.join(names)} are all on node '{node.name if node else node_id}'",
                        "evidence": {
                            "rule_id": str(rule.id), "rule_type": rule.rule_type, "strict": rule.strict,
                            "workloads": names, "workload_ids": [str(w.id) for w in group],
                            "node": node.name if node else None,
                        },
                    }
                )
        elif rule.rule_type == "keep_together" and len(by_node) > 1:
            node_names = []
            for nid in by_node:
                node = db.query(Node).filter(Node.id == nid).one_or_none()
                node_names.append(node.name if node else str(nid))
            names = [w.name or str(w.vmid) for w in linked]
            findings.append(
                {
                    "dedupe_key": f"affinity.keep_together_violated:{rule.id}",
                    "object_type": "cluster",
                    "object_id": None,
                    "category": "placement",
                    "severity": "warning",
                    "title": f"Affinity rule violated: {', '.join(names)} should be kept together but are spread across {', '.join(node_names)}",
                    "evidence": {
                        "rule_id": str(rule.id), "rule_type": rule.rule_type, "strict": rule.strict,
                        "workloads": names, "workload_ids": [str(w.id) for w in linked], "nodes": node_names,
                    },
                }
            )

    return findings


def _protection_findings(db: Session) -> list[dict]:
    """Only produced once a protection provider is actually configured --
    see routers/protection.py for the policy that gates this."""
    from .models import Policy, ProtectionResult, Workload

    require_protection = (
        db.query(Policy)
        .filter(Policy.key == "protection.require_for_all_workloads", Policy.scope_type == "organization")
        .one_or_none()
    )
    if require_protection is None or not require_protection.value.get("enabled"):
        return []

    findings = []
    protected_workload_ids = {
        r.workload_id for r in db.query(ProtectionResult).filter(ProtectionResult.protected == "true").all()
    }
    for wl in db.query(Workload).filter(Workload.is_missing.is_(False)).all():
        if wl.id not in protected_workload_ids:
            findings.append(
                {
                    "dedupe_key": f"protection.unprotected:{wl.id}",
                    "object_type": "workload",
                    "object_id": wl.id,
                    "category": "protection",
                    "severity": "warning",
                    "title": f"Workload '{wl.name or wl.vmid}' has no confirmed protection",
                    "evidence": {"vmid": wl.vmid},
                    "confidence": "moderate",
                }
            )
    return findings
