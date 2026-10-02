"""PVE read-only inventory discovery.

Repeated scans upsert existing rows (matched by natural keys) rather than
creating duplicates, and track first_seen/last_seen so historical
relationships survive an object temporarily disappearing from PVE (it is
marked is_missing=True, never hard-deleted, by a single scan).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .audit import write_audit_event
from .credentials import load_pve_credentials, resolve_pve_endpoints
from .crypto import decrypt_secret
from .metrics import collect_metrics_for_cluster
from .models import (
    Cluster,
    ClusterLogEntry,
    Node,
    PveCredential,
    PveTarget,
    PveTask,
    Provider,
    Storage,
    Workload,
)
from .pve_client import (
    PveAuthError,
    PveClient,
    PveConnectionError,
    PveCredentials,
    PveTlsError,
)

now = lambda: datetime.now(timezone.utc)


class DiscoveryError(Exception):
    def __init__(self, health: str, message: str):
        super().__init__(message)
        self.health = health
        self.message = message


def build_pve_client(db: Session, target: PveTarget, *, avoid_node_id=None) -> PveClient:
    cred = (
        db.query(PveCredential)
        .filter(PveCredential.pve_target_id == target.id, PveCredential.slot_name == "inventory")
        .one_or_none()
    )
    if cred is None:
        raise DiscoveryError("unavailable", "no 'inventory' credential slot configured")
    secret = decrypt_secret(cred.encrypted_secret)
    hosts, api_port = resolve_pve_endpoints(db, target, avoid_node_id=avoid_node_id)
    creds = PveCredentials(
        hostname=hosts[0],
        fallback_hostnames=hosts[1:],
        api_port=api_port,
        token_user=cred.token_user,
        token_id=cred.token_id,
        token_secret=secret,
        tls_verify=target.tls_verify,
    )
    return PveClient(creds), cred


def _storage_scope(entry: dict) -> str:
    stype = entry.get("type", "")
    if stype in ("pbs",):
        return "external"
    if entry.get("shared"):
        return "cluster-shared"
    return "node-local"


_DISCOVERY_LOCK_NAMESPACE = "pyxie:discovery"


def run_discovery(db: Session, target: PveTarget, actor: str = "system") -> dict:
    """Thin wrapper around _run_discovery_inner() that stops two concurrent
    discovery runs against the SAME target from racing on the same rows.
    Found live: a manual diagnostic run and the periodic 5-minute worker
    refresh both reached pve-slc-m401's large qemu list (only reachable at
    all after the client timeout fix) at close to the same moment, and the
    loser hit a raw UniqueViolation on workloads' (cluster_id, vmid)
    constraint mid-transaction instead of failing cleanly.

    Uses a SESSION-scoped Postgres advisory lock, not the transaction-
    scoped variant: _run_discovery_inner() commits multiple times
    internally (the very first thing it does is write an audit event with
    its default commit=True), so an xact-scoped lock would release at that
    first commit, long before the run actually finishes -- session-scoped
    persists across those commits and is released explicitly in `finally`
    below (and automatically by Postgres if the connection ever dies
    without that running, e.g. a hard process kill).

    Deliberately NOT the ResourceLock/Operation system write-capable
    operations use -- discovery isn't a staged Operation with an approval
    lifecycle, and a lightweight advisory lock keyed by target id is the
    right-sized tool for "don't let two of these run at once for the same
    target", not "operations fighting over a node/workload resource"."""
    got_lock = db.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:ns), hashtext(:key))"),
        {"ns": _DISCOVERY_LOCK_NAMESPACE, "key": str(target.id)},
    ).scalar()
    if not got_lock:
        return {"status": "skipped", "reason": "a discovery is already in progress for this target"}
    try:
        return _run_discovery_inner(db, target, actor)
    finally:
        db.execute(
            text("SELECT pg_advisory_unlock(hashtext(:ns), hashtext(:key))"),
            {"ns": _DISCOVERY_LOCK_NAMESPACE, "key": str(target.id)},
        )


def _run_discovery_inner(db: Session, target: PveTarget, actor: str = "system") -> dict:
    correlation_id = uuid.uuid4()
    provider = db.query(Provider).filter(Provider.id == target.provider_id).one()

    write_audit_event(
        db,
        event_category="inventory",
        event_type="inventory.discovery.started",
        actor=actor,
        actor_type="system",
        site_id=target.site_id,
        provider_id=provider.id,
        operation="discover",
        correlation_id=correlation_id,
        result="success",
        severity="info",
    )

    try:
        client, cred = build_pve_client(db, target)
    except DiscoveryError as e:
        provider.connection_health = e.health
        provider.last_error = e.message
        db.commit()
        write_audit_event(
            db,
            event_category="inventory",
            event_type="inventory.discovery.failed",
            actor=actor,
            site_id=target.site_id,
            provider_id=provider.id,
            correlation_id=correlation_id,
            result="failure",
            severity="error",
            error=e.message,
        )
        return {"status": "failed", "error": e.message}

    summary = {"clusters": 0, "nodes": 0, "workloads": 0, "storage": 0, "tasks": 0}

    try:
        with client:
            _ = client.version()
            cluster_status = client.cluster_status()

            cluster_entry = next((c for c in cluster_status if c.get("type") == "cluster"), None)
            node_entries = [c for c in cluster_status if c.get("type") == "node"]

            ip_by_node_name = {e["name"]: e.get("ip") for e in node_entries if e.get("ip")}

            cluster_name = cluster_entry["name"] if cluster_entry else f"{target.name}-standalone"
            quorate = bool(cluster_entry.get("quorate")) if cluster_entry else None

            cluster = (
                db.query(Cluster)
                .filter(Cluster.pve_target_id == target.id, Cluster.name == cluster_name)
                .one_or_none()
            )
            if cluster is None:
                cluster = Cluster(
                    site_id=target.site_id,
                    pve_target_id=target.id,
                    name=cluster_name,
                    first_seen=now(),
                )
                db.add(cluster)
                db.flush()
            cluster.quorate = quorate
            cluster.last_seen = now()
            cluster.is_missing = False
            summary["clusters"] += 1

            seen_node_ids = set()
            seen_workload_ids = set()
            seen_storage_ids = set()
            node_name_by_id: dict = {}
            # Nodes where a per-node call (qemu/lxc/storage list) failed this
            # pass -- e.g. a stuck qm process on one node hanging its own
            # /qemu listing. Used below to skip missing-reconciliation for
            # that node's existing workloads/storage: a failed FETCH is not
            # evidence of REMOVAL, and wrongly flipping is_missing=True here
            # would fire false "no longer visible to PVE" events for guests
            # that are still very much running.
            degraded_node_ids: set = set()
            degraded_reasons: list = []

            # PVE's API oddly requires Sys.Modify just to READ pending
            # updates (/nodes/{node}/apt/update), not only to change
            # anything -- confirmed from PVE's own APT.pm source. The
            # inventory credential is deliberately kept at Audit-only
            # permissions and stays that way; this borrows whatever
            # elevated PVE-side privilege the maintenance credential
            # already has for a different reason, through the SAME
            # read-only PveClient class (no write methods exist on it at
            # all, regardless of which token authenticates it -- never a
            # PveMaintenanceClient here). Does nothing if no maintenance
            # credential is configured yet for this target.
            apt_client = None
            try:
                apt_creds = load_pve_credentials(db, target, "maintenance")
                apt_client = PveClient(apt_creds)
            except Exception:
                apt_client = None

            pve_nodes = client.nodes()
            for pn in pve_nodes:
                node_name = pn["node"]
                node = (
                    db.query(Node)
                    .filter(Node.cluster_id == cluster.id, Node.name == node_name)
                    .one_or_none()
                )
                if node is None:
                    node = Node(
                        cluster_id=cluster.id,
                        site_id=target.site_id,
                        name=node_name,
                        first_seen=now(),
                    )
                    db.add(node)
                    db.flush()

                node.status = pn.get("status", "unknown")
                if pn.get("maxmem"):
                    node.mem_total_bytes = pn.get("maxmem")
                # keep the last known-good IP if this pass didn't report one
                # (e.g. the node is offline right now) -- never clear it out
                node.management_ip = ip_by_node_name.get(node_name) or node.management_ip

                try:
                    status = client.node_status(node_name)
                    cpu = status.get("cpu")
                    node.cpu_usage_pct = round(cpu * 100, 1) if cpu is not None else None
                    meminfo = status.get("memory") or {}
                    if meminfo.get("total"):
                        used_pct = meminfo.get("used", 0) / meminfo["total"] * 100
                        node.mem_usage_pct = round(used_pct, 1)
                        node.mem_total_bytes = meminfo["total"]
                    node.uptime_seconds = status.get("uptime")
                    node.kernel_version = (status.get("current-kernel") or {}).get("release")
                except Exception:
                    pass

                try:
                    ver = client.node_version(node_name)
                    node.pve_version = ver.get("version")
                except Exception:
                    pass

                if apt_client is not None:
                    try:
                        node.pending_updates = apt_client.node_apt_update_count(node_name)
                    except Exception:
                        node.pending_updates = None
                else:
                    node.pending_updates = client.node_apt_update_count(node_name)
                node.last_seen = now()
                node.is_missing = False
                seen_node_ids.add(node.id)
                node_name_by_id[node.id] = node_name
                summary["nodes"] += 1

                # workloads
                try:
                    qemu_entries = client.qemu_list(node_name) or []
                except Exception as e:
                    qemu_entries = []
                    degraded_node_ids.add(node.id)
                    degraded_reasons.append(f"{node_name}: qemu list failed ({e})")
                for vm in qemu_entries:
                    os_type = None
                    try:
                        os_type = client.qemu_config(node_name, vm["vmid"]).get("ostype")
                    except Exception:
                        pass  # config fetch failing shouldn't break discovery of everything else about this VM
                    wl = _upsert_workload(db, cluster, node, vm, "vm", os_type=os_type)
                    seen_workload_ids.add(wl.id)
                    summary["workloads"] += 1
                try:
                    lxc_entries = client.lxc_list(node_name) or []
                except Exception as e:
                    lxc_entries = []
                    degraded_node_ids.add(node.id)
                    degraded_reasons.append(f"{node_name}: lxc list failed ({e})")
                for ct in lxc_entries:
                    wl = _upsert_workload(db, cluster, node, ct, "lxc")
                    seen_workload_ids.add(wl.id)
                    summary["workloads"] += 1

                # storage
                try:
                    storage_entries = client.storage_list(node_name) or []
                except Exception as e:
                    storage_entries = []
                    degraded_node_ids.add(node.id)
                    degraded_reasons.append(f"{node_name}: storage list failed ({e})")
                for st in storage_entries:
                    s = _upsert_storage(db, target.site_id, cluster, node, st)
                    if s is not None:
                        seen_storage_ids.add(s.id)
                        summary["storage"] += 1

                # recent tasks -- not part of missing-reconciliation (append-
                # only history, no is_missing tracking), so a failure here
                # just means no new task rows this pass, nothing to protect.
                try:
                    task_entries = client.tasks(node_name, limit=20) or []
                except Exception as e:
                    task_entries = []
                    degraded_reasons.append(f"{node_name}: tasks list failed ({e})")
                for t in task_entries:
                    _upsert_task(db, cluster, node, t)
                    summary["tasks"] += 1

            if apt_client is not None:
                apt_client.close()

            cluster.node_count = len(seen_node_ids)

            # Cluster-scoped, not per-node -- PVE's own syslog-style feed
            # merged across the whole cluster (daemon restarts, corosync/
            # quorum events, hardware issues), distinct from the per-node
            # task history synced above. A failure here shouldn't break
            # the rest of discovery, same "degraded, not fatal" posture
            # as everything else in this pass.
            try:
                log_entries = client.cluster_log(max_entries=500) or []
            except Exception as e:
                log_entries = []
                degraded_reasons.append(f"cluster log fetch failed ({e})")
            for entry in log_entries:
                _upsert_cluster_log_entry(db, cluster, entry)

            # Live memory in use, taken from the CLUSTER resources list -- the same source
            # the PVE summary screen and Datacenter table use (guest-reported when the VM
            # has balloon/agent stats). The per-node VM list is NOT used for this: its
            # `mem` is the host-side process size for ballooned VMs (PAW-JChugg showed
            # 100.7% there while PVE's summary said 48.3%).
            try:
                resource_rows = client.cluster_resources("vm") or []
            except Exception as e:
                resource_rows = []
                degraded_reasons.append(f"cluster resources fetch failed ({e})")
            if resource_rows:
                live_mem = {
                    int(r["vmid"]): r.get("mem")
                    for r in resource_rows
                    if r.get("vmid") is not None and r.get("status") == "running"
                }
                for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False)).all():
                    w.mem_used_bytes = live_mem.get(w.vmid) if w.status == "running" else None

            # For any node whose qemu/lxc/storage listing failed above,
            # carry forward its existing non-missing workloads/storage into
            # the seen-sets so the reconciliation below leaves them alone.
            # We don't know their current state -- we just failed to ask --
            # and "unconfirmed" must never collapse into "confirmed gone".
            if degraded_node_ids:
                seen_workload_ids |= {
                    w.id
                    for w in db.query(Workload)
                    .filter(Workload.node_id.in_(degraded_node_ids), Workload.is_missing.is_(False))
                    .all()
                }
                seen_storage_ids |= {
                    s.id
                    for s in db.query(Storage)
                    .filter(Storage.node_id.in_(degraded_node_ids), Storage.is_missing.is_(False))
                    .all()
                }

            # mark stale objects (previously seen, not seen this pass) as
            # missing, with one audit event per object the moment it flips
            # (not re-fired every subsequent scan while it stays missing) --
            # same pattern as workloads below, extended to nodes and storage.
            # Neither has a workload-style PVE task to
            # attribute a removal to (a node leaves via `pvecm delnode`, a
            # storage entry via editing storage.cfg directly -- neither is a
            # per-vmid task PyXie already polls for), so these are always
            # actor_type="system": the "when we noticed" record still has
            # real value even without a "who".
            for n in db.query(Node).filter(Node.cluster_id == cluster.id).all():
                if n.id in seen_node_ids or n.is_missing:
                    continue
                n.is_missing = True
                write_audit_event(
                    db,
                    event_category="inventory",
                    event_type="inventory.node.missing",
                    actor_type="system",
                    cluster_id=cluster.id,
                    node_id=n.id,
                    operation="discover",
                    correlation_id=correlation_id,
                    result="success",
                    severity="warning",
                    metadata={"summary": f"{n.name} no longer visible to PVE", "name": n.name},
                    commit=False,
                )
            for s in db.query(Storage).filter(Storage.cluster_id == cluster.id).all():
                if s.id in seen_storage_ids or s.is_missing:
                    continue
                s.is_missing = True
                write_audit_event(
                    db,
                    event_category="inventory",
                    event_type="inventory.storage.missing",
                    actor_type="system",
                    cluster_id=cluster.id,
                    node_id=s.node_id,
                    operation="discover",
                    correlation_id=correlation_id,
                    result="success",
                    severity="warning",
                    metadata={
                        "summary": f"{s.name} ({s.scope}) no longer visible to PVE",
                        "name": s.name,
                        "scope": s.scope,
                    },
                    commit=False,
                )

            # Workloads get the same treatment as nodes/storage above, plus
            # best-effort user attribution -- unlike a node or storage
            # entry, a removed VM/CT usually has a matching qmdestroy/
            # vzdestroy task in PVE's own per-vmid task history, which
            # PyXie doesn't otherwise store by vmid. When PVE's task
            # retention has already aged it out, or the guest was removed
            # by unlinking config directly rather than through the API,
            # there's nothing to attribute to and the event is still
            # written with actor_type="system".
            for w in db.query(Workload).filter(Workload.cluster_id == cluster.id).all():
                if w.id in seen_workload_ids or w.is_missing:
                    continue
                w.is_missing = True
                removed_by = None
                removed_task_upid = None
                node_name = node_name_by_id.get(w.node_id)
                if node_name:
                    try:
                        recent_tasks = client.tasks(node_name, vmid=w.vmid, limit=5) or []
                    except Exception:
                        recent_tasks = []
                    destroy_task = next(
                        (
                            t
                            for t in recent_tasks
                            if t.get("type") in ("qmdestroy", "vzdestroy") and t.get("status") == "OK"
                        ),
                        None,
                    )
                    if destroy_task:
                        removed_by = destroy_task.get("user")
                        removed_task_upid = destroy_task.get("upid")
                display_name = w.name or f"vmid {w.vmid}"
                write_audit_event(
                    db,
                    event_category="inventory",
                    event_type="inventory.workload.missing",
                    actor=removed_by,
                    actor_type="provider" if removed_by else "system",
                    cluster_id=cluster.id,
                    node_id=w.node_id,
                    workload_id=w.id,
                    operation="discover",
                    correlation_id=correlation_id,
                    result="success",
                    severity="warning",
                    metadata={
                        "summary": f"{display_name} ({w.type}) no longer visible to PVE"
                        + (f" -- destroyed by {removed_by}" if removed_by else ""),
                        "name": w.name,
                        "vmid": w.vmid,
                        "type": w.type,
                        "pve_task_upid": removed_task_upid,
                    },
                    commit=False,
                )

            try:
                with db.begin_nested():  # SAVEPOINT: an RRD hiccup rolls back
                    # only the metrics collection, never the inventory
                    # upserts already staged above in this same transaction.
                    metrics_summary = collect_metrics_for_cluster(db, client, cluster.id)
                summary["metric_points"] = metrics_summary["points"]
            except Exception:
                pass

        # A per-node call failing (e.g. a stuck qm process hanging that
        # node's own /qemu listing) no longer fails discovery outright --
        # everything else still gets recorded, and this is surfaced as a
        # "warning" health instead of silently reporting fully "connected".
        if degraded_reasons:
            provider.connection_health = "warning"
            provider.last_error = "partial discovery -- " + "; ".join(degraded_reasons)
        else:
            provider.connection_health = "connected"
            provider.last_error = None
        target.endpoint_status = _endpoint_status(client)
        provider.last_success_at = now()
        cred.status = "valid"
        cred.last_validated_at = now()
        db.commit()

        write_audit_event(
            db,
            event_category="inventory",
            event_type="inventory.discovery.completed",
            actor=actor,
            site_id=target.site_id,
            cluster_id=cluster.id,
            provider_id=provider.id,
            correlation_id=correlation_id,
            result="success",
            severity="warning" if degraded_reasons else "info",
            metadata={**summary, "degraded": degraded_reasons} if degraded_reasons else summary,
        )
        return {"status": "ok", **summary, **({"degraded": degraded_reasons} if degraded_reasons else {})}

    except PveTlsError as e:
        return _fail(db, provider, cred, target, correlation_id, actor, "tls_error", str(e), client=client)
    except PveAuthError as e:
        return _fail(db, provider, cred, target, correlation_id, actor, "authentication_failed", str(e), client=client)
    except PveConnectionError as e:
        return _fail(db, provider, cred, target, correlation_id, actor, "unavailable", str(e), client=client)
    except Exception as e:  # noqa: BLE001 - surface unexpected provider errors, don't swallow
        return _fail(db, provider, cred, target, correlation_id, actor, "unavailable", str(e), client=client)


def _endpoint_status(client, error=None) -> dict:
    """Which API endpoint a discovery run actually used, and which configured
    cluster members could not be connected to. Read by the Integrations page
    and by findings.py (backup-endpoint / unreachable findings)."""
    return {
        "active": None if error else client.active_host,
        "preferred": client.preferred_host,
        "unhealthy": client.unhealthy_endpoints,
        "error": error[:300] if error else None,
        "checked_at": now().isoformat(),
    }


def _fail(db, provider, cred, target, correlation_id, actor, health, message, client=None):
    db.rollback()
    if client is not None:
        target.endpoint_status = _endpoint_status(client, error=message)
    provider.connection_health = health
    provider.last_error = message
    cred.status = "invalid" if health == "authentication_failed" else cred.status
    db.commit()
    write_audit_event(
        db,
        event_category="inventory",
        event_type="inventory.discovery.failed",
        actor=actor,
        site_id=target.site_id,
        provider_id=provider.id,
        correlation_id=correlation_id,
        result="failure",
        severity="error",
        error=message,
    )
    return {"status": "failed", "error": message, "connection_health": health}


def _upsert_workload(db: Session, cluster: Cluster, node: Node, data: dict, wtype: str, os_type: str | None = None) -> Workload:
    vmid = int(data["vmid"])
    wl = (
        db.query(Workload)
        .filter(Workload.cluster_id == cluster.id, Workload.vmid == vmid)
        .one_or_none()
    )
    if wl is None:
        wl = Workload(
            cluster_id=cluster.id,
            site_id=cluster.site_id,
            node_id=node.id,
            vmid=vmid,
            type=wtype,
            first_seen=now(),
        )
        db.add(wl)
        db.flush()
    wl.node_id = node.id
    wl.name = data.get("name")
    wl.status = data.get("status", "unknown")
    wl.cpu_cores = data.get("cpus")
    wl.memory_bytes = data.get("maxmem")
    if os_type is not None:
        wl.os_type = os_type
    tags = data.get("tags")
    wl.tags = tags.split(";") if tags else []
    wl.ha_state = (data.get("hastate") or None)
    wl.last_seen = now()
    wl.is_missing = False
    return wl


def _upsert_storage(db: Session, site_id, cluster: Cluster, node: Node, data: dict) -> Storage | None:
    name = data["storage"]
    scope = _storage_scope(data)
    query = db.query(Storage).filter(Storage.site_id == site_id, Storage.name == name)
    if scope == "node-local":
        query = query.filter(Storage.node_id == node.id)
    else:
        query = query.filter(Storage.cluster_id == cluster.id, Storage.node_id.is_(None))
    st = query.one_or_none()

    if st is None and scope == "node-local" and not data.get("active"):
        # PVE lists a node-local storage under every node whose storage.cfg
        # entry isn't node-restricted, even nodes where it isn't physically
        # present (active=0 there). Don't create a phantom "unavailable" row
        # for a node that never actually had this storage -- only track it
        # once it's seen active on some node (its real home).
        return None

    if st is None:
        st = Storage(
            site_id=site_id,
            cluster_id=cluster.id,
            node_id=node.id if scope == "node-local" else None,
            name=name,
            scope=scope,
            first_seen=now(),
        )
        db.add(st)
        db.flush()
    st.type = data.get("type")
    st.capacity_bytes = data.get("total")
    st.used_bytes = data.get("used")
    st.status = "available" if data.get("active") else "unavailable"
    st.last_seen = now()
    st.is_missing = False
    return st


def _upsert_task(db: Session, cluster: Cluster, node: Node, data: dict):
    upid = data.get("upid")
    if not upid:
        return
    task = db.query(PveTask).filter(PveTask.upid == upid).one_or_none()
    if task is None:
        task = PveTask(cluster_id=cluster.id, node_id=node.id, upid=upid, first_seen=now())
        db.add(task)
    task.task_type = data.get("type")
    task.status = data.get("status")
    task.user = data.get("user")
    if data.get("starttime"):
        task.started_at = datetime.fromtimestamp(data["starttime"], tz=timezone.utc)
    if data.get("endtime"):
        task.ended_at = datetime.fromtimestamp(data["endtime"], tz=timezone.utc)
    # PVE's task list reports "running" (or omits status) for a still-in-
    # flight task -- that's the only case worth preserving the old value
    # for. "OK" is a real terminal status and must be recorded, not treated
    # like a null read; leaving it out of exit_status left every task that
    # finished successfully before PyXie's next poll saw it stuck at NULL
    # forever, indistinguishable from "still running" to anything checking
    # "has this task finished, and how".
    if data.get("status") not in (None, "running"):
        task.exit_status = data.get("status")
    task.last_seen = now()


def _upsert_cluster_log_entry(db: Session, cluster: Cluster, data: dict):
    # PVE's own "id" is the natural dedupe key for one log line -- an
    # append-only feed, so once a pve_id has been seen there is never
    # anything to update on it, only new ones to insert. Falls back to a
    # synthetic node+timestamp+sequence key on the rare chance PVE omits
    # id, rather than dropping the entry.
    pve_id = data.get("id") or f"{data.get('node')}:{data.get('t')}:{data.get('n')}"
    exists = (
        db.query(ClusterLogEntry.id)
        .filter(ClusterLogEntry.cluster_id == cluster.id, ClusterLogEntry.pve_id == pve_id)
        .first()
    )
    if exists is not None:
        return
    db.add(
        ClusterLogEntry(
            cluster_id=cluster.id,
            pve_id=pve_id,
            node=data.get("node"),
            tag=data.get("tag"),
            priority=data.get("pri"),
            message=data.get("msg"),
            logged_at=datetime.fromtimestamp(data["t"], tz=timezone.utc) if data.get("t") else None,
            first_seen=now(),
        )
    )
