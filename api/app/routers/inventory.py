import base64
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from datetime import datetime, timedelta, timezone

from pyxie_core.audit import write_audit_event
from pyxie_core.discovery import build_pve_client
from pyxie_core.maintenance import _qemu_config, _workload_disk_storage_names
from pyxie_core.metrics import latest_workload_metrics
from pyxie_core.credentials import (
    CredentialNotConfigured,
    HostNotAddressable,
    load_host_maintenance_credentials,
    load_pve_credentials,
)
from pyxie_core.pve_client import PveClient
from pyxie_core.pve_write_client import PveMaintenanceClient
from pyxie_core.host_maintenance_client import (
    HostKeyMismatchError,
    HostKeyNotPinnedError,
    HostMaintenanceClient,
    HostMaintenanceConnectionError,
    HostMaintenanceProtocolError,
    SUPPORTED_CONTRACT_VERSION,
    probe_host_key,
)
from pyxie_core.models import (
    AppSettings,
    Cluster,
    ClusterLogEntry,
    HostMaintenanceCredential,
    Node,
    Operation,
    Organization,
    PveTarget,
    PveTask,
    Site,
    Storage,
    Workload,
)

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .. import schemas

router = APIRouter(prefix="/api", tags=["inventory"], dependencies=[Depends(get_current_user)])


@router.get("/organizations", response_model=list[schemas.OrganizationOut])
def list_organizations(db: Session = Depends(get_db)):
    return db.query(Organization).order_by(Organization.name).all()


@router.patch("/organizations/{org_id}", response_model=schemas.OrganizationOut, dependencies=[Depends(require_admin)])
def update_organization(
    org_id: uuid.UUID, payload: schemas.OrganizationUpdate,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Rename the deployment's organization. PyXie is one org per deployment
    (a fresh install seeds a single placeholder org/site row) -- this is how
    that placeholder gets turned into the real company name, there's no
    separate 'create organization' endpoint since a second one is never
    needed within a single deployment."""
    org = db.query(Organization).filter(Organization.id == org_id).one_or_none()
    if org is None:
        raise HTTPException(404, "Organization not found")
    before = {"name": org.name, "slug": org.slug}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "an organization with that slug already exists")
    write_audit_event(
        db,
        event_category="organization",
        event_type="organization.updated",
        actor=user.email, actor_type="user",
        state_before=before,
        state_after={"name": org.name, "slug": org.slug},
    )
    db.commit()
    db.refresh(org)
    return org


@router.get("/sites", response_model=list[schemas.SiteOut])
def list_sites(db: Session = Depends(get_db)):
    return db.query(Site).order_by(Site.name).all()


@router.post("/sites", response_model=schemas.SiteOut, dependencies=[Depends(require_admin)])
def create_site(payload: schemas.SiteCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == payload.organization_id).one_or_none()
    if org is None:
        raise HTTPException(404, "Organization not found")
    site = Site(organization_id=payload.organization_id, name=payload.name, slug=payload.slug)
    db.add(site)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "a site with that slug already exists in this organization")
    write_audit_event(
        db,
        event_category="site",
        event_type="site.created",
        actor=user.email, actor_type="user",
        site_id=site.id,
        state_after={"name": site.name, "slug": site.slug, "organization_id": str(payload.organization_id)},
    )
    db.commit()
    db.refresh(site)
    return site


@router.patch("/sites/{site_id}", response_model=schemas.SiteOut, dependencies=[Depends(require_admin)])
def update_site(site_id: uuid.UUID, payload: schemas.SiteUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).one_or_none()
    if site is None:
        raise HTTPException(404, "Site not found")
    before = {"name": site.name, "slug": site.slug}
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(site, field, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "a site with that slug already exists in this organization")
    write_audit_event(
        db,
        event_category="site",
        event_type="site.updated",
        actor=user.email, actor_type="user",
        site_id=site.id,
        state_before=before,
        state_after={"name": site.name, "slug": site.slug},
    )
    db.commit()
    db.refresh(site)
    return site


@router.get("/clusters", response_model=list[schemas.ClusterOut])
def list_clusters(db: Session = Depends(get_db)):
    return db.query(Cluster).order_by(Cluster.name).all()


@router.get("/clusters/{cluster_id}", response_model=schemas.ClusterOut)
def get_cluster(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Cluster).filter(Cluster.id == cluster_id).one()


def _attach_allocated_memory(db: Session, nodes: list[Node]) -> None:
    """Sum of running workloads' configured memory_bytes, grouped by node --
    not a real column, attached here so NodeOut (from_attributes) can pick
    it up via plain getattr. Same allocation-based measure the migration
    safety checks (placement.py) use, so the UI's "tight" warning means
    the same thing as what will actually block a migration."""
    node_ids = [n.id for n in nodes]
    if not node_ids:
        return
    rows = (
        db.query(Workload.node_id, func.sum(Workload.memory_bytes))
        .filter(Workload.node_id.in_(node_ids), Workload.is_missing.is_(False), Workload.status == "running")
        .group_by(Workload.node_id)
        .all()
    )
    by_node = {node_id: total or 0 for node_id, total in rows}
    for n in nodes:
        n.allocated_memory_bytes = by_node.get(n.id, 0)


@router.get("/nodes", response_model=list[schemas.NodeOut])
def list_nodes(db: Session = Depends(get_db)):
    nodes = db.query(Node).order_by(Node.name).all()
    _attach_allocated_memory(db, nodes)
    return nodes


@router.get("/nodes/{node_id}", response_model=schemas.NodeOut)
def get_node(node_id: uuid.UUID, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.id == node_id).one()
    _attach_allocated_memory(db, [node])
    return node


@router.get("/nodes/{node_id}/workloads", response_model=list[schemas.WorkloadOut])
def get_node_workloads(node_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Workload).filter(Workload.node_id == node_id).order_by(Workload.vmid).all()


@router.get("/nodes/{node_id}/pending-updates")
def get_node_pending_updates(node_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live, on-demand full package list for one node -- read-only, via the
    same `maintenance` PVE API token already used for the pending_updates
    COUNT surfaced elsewhere (Nodes table, node detail). Deliberately does
    NOT require the separate SSH host-maintenance wrapper: PVE's own API
    already returns full package detail (Package/OldVersion/Version/...)
    from this same endpoint, that data was just being discarded down to a
    length. This has no bearing on actually applying updates -- PVE's API
    has no endpoint for that, only listing; that's still what the SSH
    wrapper is for."""
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one_or_none()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none() if cluster else None
    if target is None:
        raise HTTPException(404, "pve target not found for this node")

    try:
        creds = load_pve_credentials(db, target, "maintenance")
    except CredentialNotConfigured:
        raise HTTPException(409, "no 'maintenance' credential configured for this target yet")

    try:
        client = PveClient(creds)
        with client:
            packages = client.node_apt_updates(node.name)
    except Exception as e:
        raise HTTPException(502, f"could not reach PVE: {e}")

    return {"node": node.name, "packages": packages}


@router.post("/nodes/{node_id}/ssh-host-key/probe", dependencies=[Depends(require_admin)])
def probe_node_ssh_host_key(node_id: uuid.UUID, db: Session = Depends(get_db)):
    """Read-only network probe -- connects at the SSH transport level ONLY,
    no authentication, and returns the fingerprint for the OPERATOR to
    compare against the host's own console output. Never pins anything by
    itself; see the PUT endpoint below."""
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    if not node.management_ip:
        raise HTTPException(409, "no management_ip on record for this node yet -- run discovery first")
    try:
        key_type, key_base64, fingerprint = probe_host_key(node.management_ip)
    except HostMaintenanceConnectionError as exc:
        raise HTTPException(502, f"could not reach {node.management_ip}: {exc}")
    return {"key_type": key_type, "key_base64": key_base64, "fingerprint": fingerprint}


class PinHostKeyRequest(BaseModel):
    key_type: str
    key_base64: str


@router.put("/nodes/{node_id}/ssh-host-key", dependencies=[Depends(require_admin)])
def pin_node_ssh_host_key(
    node_id: uuid.UUID, payload: PinHostKeyRequest,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Explicit, operator-confirmed pin -- the caller is expected to have
    already compared the probe's fingerprint against the host's own
    console output before calling this. Never invoked automatically by
    anything else in this app."""
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    node.ssh_host_key_type = payload.key_type
    node.ssh_host_key_base64 = payload.key_base64
    node.ssh_host_key_fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(base64.b64decode(payload.key_base64)).digest()
    ).decode().rstrip("=")
    node.ssh_host_key_pinned_at = datetime.now(timezone.utc)
    node.ssh_host_key_pinned_by = user.email
    write_audit_event(
        db, event_category="credential", event_type="node.ssh_host_key_pinned",
        actor=user.email, actor_type="user", site_id=node.site_id,
        metadata={"node_id": str(node_id), "fingerprint": node.ssh_host_key_fingerprint},
    )
    db.commit()
    return {"pinned": True, "fingerprint": node.ssh_host_key_fingerprint}


@router.delete("/nodes/{node_id}/ssh-host-key", dependencies=[Depends(require_admin)])
def disconnect_node_ssh_host_key(
    node_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """'Disconnect' -- app-side only. Clears the pinned host key, which
    means PyXie's strict-pinning check refuses to reach this node again
    until it's re-probed and re-pinned. Does NOT touch the node itself --
    the pyxie-hostmaint user/wrapper/sudoers stay in place there until
    someone runs uninstall.sh (see the host-maintenance-uninstall-script
    endpoint on the pve-targets router). Reversible from the UI alone."""
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    was_pinned = bool(node.ssh_host_key_base64)
    node.ssh_host_key_type = None
    node.ssh_host_key_base64 = None
    node.ssh_host_key_fingerprint = None
    node.ssh_host_key_pinned_at = None
    node.ssh_host_key_pinned_by = None
    write_audit_event(
        db, event_category="credential", event_type="node.ssh_host_key_unpinned",
        actor=user.email, actor_type="user", site_id=node.site_id,
        metadata={"node_id": str(node_id), "was_pinned": was_pinned},
    )
    db.commit()
    return {"disconnected": True}


@router.get("/nodes/{node_id}/host-maintenance-status")
def get_node_host_maintenance_status(node_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live readiness check for Stage W4 on this specific node -- never
    cached, always a fresh (short-timeout) probe, since 'is this actually
    reachable right now' is the whole point."""
    node = db.query(Node).filter(Node.id == node_id).one_or_none()
    if node is None:
        raise HTTPException(404, "node not found")
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()

    cred_row = (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target.id)
        .one_or_none()
    )
    base = {
        "node": node.name,
        "credential_configured": cred_row is not None,
        "management_ip_known": bool(node.management_ip),
        "host_key_pinned": bool(node.ssh_host_key_base64),
        "host_key_fingerprint": node.ssh_host_key_fingerprint,
        "host_key_pinned_at": node.ssh_host_key_pinned_at,
    }
    if cred_row is None or not node.management_ip or not node.ssh_host_key_base64:
        return {**base, "provisioned": False, "reachable": False}

    try:
        creds = load_host_maintenance_credentials(db, target, node)
    except (CredentialNotConfigured, HostNotAddressable) as exc:
        return {**base, "provisioned": False, "reachable": False, "error": str(exc)}

    try:
        with HostMaintenanceClient(creds, connect_timeout=5.0) as client:
            version_info = client.version()
            status_info = client.status()
    except (HostKeyMismatchError, HostKeyNotPinnedError) as exc:
        return {**base, "provisioned": True, "reachable": False, "error": f"HOST KEY MISMATCH: {exc}"}
    except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
        return {**base, "provisioned": True, "reachable": False, "error": str(exc)}

    contract_version = version_info.get("contract_version")
    from pyxie_core import host_kit as _hk
    from .host_kit import KIT_DIR as _KIT_DIR
    kit_wrapper = _hk.wrapper_version(_KIT_DIR)
    node.wrapper_version = version_info.get("wrapper_version")
    node.wrapper_checked_at = datetime.now(timezone.utc)
    cred_row.last_seen_wrapper_version = version_info.get("wrapper_version")  # keep the credential's copy fresh too
    cred_row.last_seen_contract_version = contract_version
    db.commit()
    return {
        **base,
        "provisioned": True,
        "reachable": True,
        "wrapper_version": version_info.get("wrapper_version"),
        "kit_wrapper_version": kit_wrapper,
        "wrapper_outdated": _hk.is_outdated(version_info.get("wrapper_version"), kit_wrapper),
        "contract_version": contract_version,
        "contract_compatible": contract_version == SUPPORTED_CONTRACT_VERSION,
        "capabilities": version_info.get("capabilities", []),
        "kernel_version": status_info.get("kernel_version"),
        "upgradable_count": status_info.get("upgradable_count"),
        "reboot_required": status_info.get("reboot_required"),
        "disk_free_bytes": status_info.get("disk_free_bytes"),
        "status_checked_at": status_info.get("checked_at"),
    }


@router.get("/nodes/{node_id}/tasks", response_model=list[schemas.PveTaskOut])
def get_node_tasks(node_id: uuid.UUID, db: Session = Depends(get_db)):
    return (
        db.query(PveTask)
        .filter(PveTask.node_id == node_id)
        .order_by(PveTask.started_at.desc().nullslast())
        .limit(20)
        .all()
    )


@router.get("/workloads", response_model=list[schemas.WorkloadOut])
def list_workloads(db: Session = Depends(get_db), type: str | None = None):
    q = db.query(Workload)
    if type:
        q = q.filter(Workload.type == type)
    return q.order_by(Workload.vmid).all()


@router.get("/workloads/latest-metrics")
def workloads_latest_metrics(db: Session = Depends(get_db)):
    """cpu_pct/mem_pct as of the most recent poll, keyed by workload id --
    for quick-glance meters (Maintenance page). Not live -- same 'as of
    last discovery cycle' freshness a node's own cpu_usage_pct/mem_usage_pct
    columns already imply."""
    return latest_workload_metrics(db)


@router.get("/workloads/current-storage")
def workloads_current_storage(db: Session = Depends(get_db)):
    """Each VM's actual current storage, parsed live from its qemu config --
    Storage isn't linked to a specific workload in the DB, only to a
    node/scope, so this is the only way to know which storage a given VM's
    disk(s) actually sit on right now. Batches one PVE client connection
    per cluster (not per workload). LXC/unreadable-config workloads are
    simply omitted, not reported as an error -- same fail-quiet-and-skip
    posture as everywhere else this pattern is used."""
    out: dict[str, dict] = {}
    workloads = db.query(Workload).filter(Workload.is_missing.is_(False), Workload.type == "vm").all()
    by_cluster: dict = {}
    for w in workloads:
        by_cluster.setdefault(w.cluster_id, []).append(w)

    node_cache: dict = {}
    storage_scope_cache: dict = {}

    def scope_for(name: str, node_id) -> str | None:
        key = (name, node_id)
        if key not in storage_scope_cache:
            row = (
                db.query(Storage).filter(Storage.name == name, Storage.node_id == node_id).one_or_none()
                or db.query(Storage).filter(Storage.name == name, Storage.scope == "cluster-shared").one_or_none()
            )
            storage_scope_cache[key] = row.scope if row else None
        return storage_scope_cache[key]

    for cluster_id, cluster_workloads in by_cluster.items():
        cluster = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
        if cluster is None:
            continue
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
        if target is None:
            continue
        try:
            client, _cred = build_pve_client(db, target)
        except Exception:
            continue
        with client:
            for w in cluster_workloads:
                node = node_cache.get(w.node_id)
                if node is None:
                    node = db.query(Node).filter(Node.id == w.node_id).one_or_none()
                    node_cache[w.node_id] = node
                if node is None:
                    continue
                config = _qemu_config(client, node.name, w)
                if config is None:
                    continue
                names = sorted(_workload_disk_storage_names(config))
                if not names:
                    continue
                primary = names[0]
                out[str(w.id)] = {"name": primary, "scope": scope_for(primary, node.id)}
    return out


class WorkloadPlacementProfileUpdate(BaseModel):
    sensitivity: str | None = None  # standard | restricted
    downtime_tolerance: str | None = None  # low | standard | high
    placement_notes: str | None = None  # PyXie-only free text -- why, not synced from/to PVE
    storage_preference: str | None = "__unset__"  # 'local' | 'shared' | null -- sentinel default lets a real null clear it explicitly
    do_not_move: bool | None = None  # never moved by Balance Load / automatic balancing
    preferred_node_id: str | None = "__unset__"  # sticky soft preferred host (node UUID string), or null to clear it -- kept as str (not uuid.UUID) so the "__unset__" sentinel default doesn't fail UUID validation; parsed below


@router.patch("/workloads/{workload_id}/placement-profile", response_model=schemas.WorkloadOut, dependencies=[Depends(require_admin)])
def set_workload_placement_profile(
    workload_id: uuid.UUID, payload: WorkloadPlacementProfileUpdate,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    if payload.sensitivity is not None and payload.sensitivity not in ("standard", "restricted"):
        raise HTTPException(400, "sensitivity must be 'standard' or 'restricted'")
    if payload.downtime_tolerance is not None and payload.downtime_tolerance not in ("low", "standard", "high"):
        raise HTTPException(400, "downtime_tolerance must be 'low', 'standard', or 'high'")
    if payload.storage_preference not in ("__unset__", None, "local", "shared"):
        raise HTTPException(400, "storage_preference must be 'local', 'shared', or null")
    wl = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if wl is None:
        raise HTTPException(404, "workload not found")
    preferred_node_id: uuid.UUID | None = None
    if payload.preferred_node_id != "__unset__" and payload.preferred_node_id is not None:
        try:
            preferred_node_id = uuid.UUID(payload.preferred_node_id)
        except ValueError:
            raise HTTPException(400, "preferred_node_id must be a valid node UUID or null")
        if db.query(Node).filter(Node.id == preferred_node_id, Node.cluster_id == wl.cluster_id).one_or_none() is None:
            raise HTTPException(400, "preferred_node_id must be a node in this workload's own cluster")

    before = {
        "sensitivity": wl.sensitivity, "downtime_tolerance": wl.downtime_tolerance,
        "placement_notes": wl.placement_notes, "storage_preference": wl.storage_preference,
        "preferred_node_id": str(wl.preferred_node_id) if wl.preferred_node_id else None,
        "do_not_move": wl.do_not_move,
    }
    if payload.do_not_move is not None:
        wl.do_not_move = payload.do_not_move
    if payload.sensitivity is not None:
        wl.sensitivity = payload.sensitivity
    if payload.downtime_tolerance is not None:
        wl.downtime_tolerance = payload.downtime_tolerance
    if payload.placement_notes is not None:
        wl.placement_notes = payload.placement_notes
    if payload.storage_preference != "__unset__":
        wl.storage_preference = payload.storage_preference
    if payload.preferred_node_id != "__unset__":
        wl.preferred_node_id = preferred_node_id
    db.commit()
    db.refresh(wl)
    write_audit_event(
        db, event_category="settings", event_type="workload.placement_profile_changed",
        actor=user.email, actor_type="user", workload_id=wl.id,
        state_before=before,
        state_after={
            "sensitivity": wl.sensitivity, "downtime_tolerance": wl.downtime_tolerance,
            "placement_notes": wl.placement_notes, "storage_preference": wl.storage_preference,
            "preferred_node_id": str(wl.preferred_node_id) if wl.preferred_node_id else None,
            "do_not_move": wl.do_not_move,
        },
    )
    return wl


@router.get("/storage", response_model=list[schemas.StorageOut])
def list_storage(db: Session = Depends(get_db)):
    return db.query(Storage).order_by(Storage.name).all()


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db), limit: int = 50, status: str | None = None):
    """status="failed" filters to tasks whose exit status wasn't OK/running
    -- the diagnostic list (Phil: historical job failures like snapshot
    errors, surfaced separately). Also resolves each task's UPID-embedded
    vmid to the workload's real name, not just the raw id -- previously
    only the raw vmid was ever shown (parsed client-side from the UPID
    string), never a name."""
    q = db.query(PveTask)
    if status == "failed":
        # exit_status, not status -- same field findings.py's task.failed
        # check uses: it's the sticky terminal value (never reverts to
        # None/"running" once a task has finished at least once), where
        # status can legitimately be in-flight state on a later poll.
        q = q.filter(PveTask.exit_status.isnot(None), PveTask.exit_status != "OK")
    tasks = q.order_by(PveTask.started_at.desc().nullslast()).limit(min(limit, 500)).all()

    # UPID format: UPID:node:pid:pstart:starttime:type:id:user: -- the 7th
    # segment ("id") is the vmid/CT id a VM-scoped task acted on, empty for
    # a node-level task (host reboot, storage rescan, etc). Keyed by
    # (cluster_id, vmid), NOT (node_id, vmid) -- PVE enforces vmid
    # uniqueness cluster-wide, not per-node, and a migration task is
    # recorded on the SOURCE node while Workload.node_id reflects wherever
    # the VM lives NOW (the destination, once the migration succeeded) --
    # confirmed live: keying by node_id silently failed to resolve every
    # migrated VM's own migration tasks, the one case this table exists to
    # cover.
    vmid_by_task_id: dict = {}
    lookups: set = set()
    for t in tasks:
        parts = t.upid.split(":")
        vmid_str = parts[6] if len(parts) > 6 else ""
        if vmid_str.isdigit():
            vmid = int(vmid_str)
            vmid_by_task_id[t.id] = vmid
            lookups.add((t.cluster_id, vmid))

    name_by_cluster_vmid: dict = {}
    workload_id_by_cluster_vmid: dict = {}
    if lookups:
        cluster_ids = {cid for cid, _ in lookups}
        rows = db.query(Workload.cluster_id, Workload.vmid, Workload.name, Workload.id).filter(Workload.cluster_id.in_(cluster_ids)).all()
        for cluster_id, vmid, name, wid in rows:
            name_by_cluster_vmid[(cluster_id, vmid)] = name
            workload_id_by_cluster_vmid[(cluster_id, vmid)] = wid

    # PVE only ever shows a task as run by whichever credential/token
    # PyXie used (e.g. pyxie-maint@pve) -- Phil: "who in PyXie actually
    # asked for this" needs the Operation behind it. NOT matched via
    # Operation.pve_upid despite the column existing: confirmed live that
    # every completed vm.live_migrate has pve_upid=NULL --
    # migration_workflow.py deliberately clears it between stages (one
    # migration can issue several distinct PVE tasks -- shutdown, migrate,
    # startup -- tracking one UPID at a time, not accumulating history),
    # so it's a live in-flight pointer, not a permanent record. Matched
    # instead by object (workload/node) + time overlap: does the task's
    # start time fall inside the operation's own [created_at, completed_at]
    # window. Heuristic, not a hard foreign key -- but two operations
    # concurrently targeting the exact same workload/node practically
    # don't happen (locks.py's same-target lock prevents it), so object +
    # time-window is reliable in practice.
    relevant_workload_ids = set(workload_id_by_cluster_vmid.values())
    relevant_node_ids = {t.node_id for t in tasks if t.node_id}
    op_candidates: list = []
    if relevant_workload_ids or relevant_node_ids:
        conds = []
        if relevant_workload_ids:
            conds.append(Operation.workload_id.in_(relevant_workload_ids))
        if relevant_node_ids:
            conds.append(Operation.node_id.in_(relevant_node_ids))
        op_candidates = db.query(Operation).filter(or_(*conds)).order_by(Operation.created_at.desc()).limit(500).all()

    def _find_operation(task: PveTask, vmid: int | None):
        if not task.started_at:
            return None
        # A VM-scoped task matches on its workload's stable id (works even
        # for a migration, since workload_id doesn't change when the VM
        # moves -- only the vmid->workload lookup that FINDS it has to be
        # cluster-scoped, not node-scoped, see above). A node-level task
        # (no vmid at all) matches on the operation's own node_id instead.
        wid = workload_id_by_cluster_vmid.get((task.cluster_id, vmid)) if vmid else None
        best, best_delta = None, None
        for op in op_candidates:
            is_candidate = (wid and op.workload_id == wid) or (not wid and task.node_id and op.node_id == task.node_id)
            if not is_candidate:
                continue
            window_start = op.created_at
            window_end = op.completed_at or datetime.now(timezone.utc)
            buffer = timedelta(minutes=1)
            if window_start - buffer <= task.started_at <= window_end + buffer:
                delta = abs((task.started_at - op.created_at).total_seconds())
                if best is None or delta < best_delta:
                    best, best_delta = op, delta
        return best

    out = []
    for t in tasks:
        vmid = vmid_by_task_id.get(t.id)
        op = _find_operation(t, vmid)
        out.append(
            {
                "id": str(t.id),
                "cluster_id": str(t.cluster_id),
                "node_id": str(t.node_id) if t.node_id else None,
                "upid": t.upid,
                "task_type": t.task_type,
                "status": t.status,
                "exit_status": t.exit_status,
                "user": t.user,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "ended_at": t.ended_at.isoformat() if t.ended_at else None,
                "vmid": vmid,
                "workload_name": name_by_cluster_vmid.get((t.cluster_id, vmid)) if vmid else None,
                "operation_id": str(op.id) if op else None,
                "operation_type_id": op.operation_type_id if op else None,
                "initiated_by": (op.approved_by or op.created_by) if op else None,
            }
        )
    return out


@router.get("/cluster-log")
def list_cluster_log(
    db: Session = Depends(get_db),
    limit: int = 100,
    node: str | None = None,
    max_priority: int | None = None,
):
    """PVE's own syslog-style cluster log (see PveClient.cluster_log()) --
    ambient system activity, distinct from PVE Tasks (job outcomes) and
    Audit Log (what PyXie itself did). max_priority filters to that
    severity or worse (lower pri number = more severe, standard syslog
    scale) -- e.g. max_priority=3 shows err/crit/alert/emerg only."""
    q = db.query(ClusterLogEntry)
    if node:
        q = q.filter(ClusterLogEntry.node == node)
    if max_priority is not None:
        q = q.filter(ClusterLogEntry.priority.isnot(None), ClusterLogEntry.priority <= max_priority)
    entries = q.order_by(ClusterLogEntry.logged_at.desc().nullslast()).limit(min(limit, 500)).all()
    return [
        {
            "id": str(e.id),
            "cluster_id": str(e.cluster_id),
            "node": e.node,
            "tag": e.tag,
            "priority": e.priority,
            "message": e.message,
            "logged_at": e.logged_at.isoformat() if e.logged_at else None,
        }
        for e in entries
    ]


@router.get("/tasks/{task_id}/log")
def get_task_log(task_id: uuid.UUID, db: Session = Depends(get_db)):
    """On-demand fetch of PVE's own full task log -- the task list itself
    only ever stores PVE's short summary status (e.g. "OK" or a one-line
    error), never the full log lines a real diagnosis needs. Requires a
    'maintenance'-tier credential for this task's target (the same slot
    live-migration progress-polling already uses for task_log() -- see
    migration_workflow.py); 'inventory'-only targets get a clear message
    instead of a 403, not a raw credential error."""
    task = db.query(PveTask).filter(PveTask.id == task_id).one_or_none()
    if task is None:
        raise HTTPException(404, "task not found")
    if task.node_id is None:
        return {"error": "This task has no associated node (already missing from inventory).", "lines": []}
    node = db.query(Node).filter(Node.id == task.node_id).one_or_none()
    cluster = db.query(Cluster).filter(Cluster.id == task.cluster_id).one_or_none()
    if node is None or cluster is None:
        return {"error": "The node or cluster this task ran on is no longer known to PyXie.", "lines": []}
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one_or_none()
    if target is None:
        return {"error": "The PVE target this task ran on is no longer configured.", "lines": []}

    try:
        creds = load_pve_credentials(db, target, "maintenance")
    except CredentialNotConfigured:
        return {
            "error": "Full task log requires a 'maintenance' credential configured for this PVE target -- see the Credentials page.",
            "lines": [],
        }

    try:
        with PveMaintenanceClient(creds) as client:
            lines = client.task_log(node.name, task.upid, start=0, limit=500)
    except Exception as e:
        return {"error": str(e), "lines": []}
    return {"error": None, "lines": lines}


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    clusters = db.query(Cluster).filter(Cluster.is_missing.is_(False)).all()
    nodes = db.query(Node).filter(Node.is_missing.is_(False)).all()
    workloads = db.query(Workload).filter(Workload.is_missing.is_(False)).all()
    storage = db.query(Storage).filter(Storage.is_missing.is_(False)).all()

    nodes_online = sum(1 for n in nodes if n.status == "online")
    workloads_running = sum(1 for w in workloads if w.status == "running")
    nodes_with_updates = sum(1 for n in nodes if (n.pending_updates or 0) > 0)
    nodes_in_maintenance = sum(1 for n in nodes if n.maintenance_mode)

    operations_awaiting_approval = (
        db.query(func.count(Operation.id))
        .filter(Operation.status == "awaiting_approval", Operation.dismissed.is_(False))
        .scalar()
    ) or 0

    failed_tasks = (
        db.query(func.count(PveTask.id))
        .filter(PveTask.exit_status.isnot(None), PveTask.exit_status != "OK")
        .scalar()
    )

    total_capacity = sum((s.capacity_bytes or 0) for s in storage)
    total_used = sum((s.used_bytes or 0) for s in storage)
    storage_pct = round(total_used / total_capacity * 100, 1) if total_capacity else None

    # Totals behind the memory gauge's detail line: physical RAM across nodes, and the
    # part in use (each node's reported percentage applied to its own total).
    mem_total = sum((n.mem_total_bytes or 0) for n in nodes)
    mem_used = sum((n.mem_total_bytes or 0) * (n.mem_usage_pct or 0) / 100 for n in nodes)

    cluster_status = "healthy"
    for c in clusters:
        if c.quorate is False:
            cluster_status = "critical"
            break
    if cluster_status == "healthy" and nodes_online < len(nodes):
        cluster_status = "warning"

    return {
        "counts": {
            "clusters": len(clusters),
            "nodes": len(nodes),
            "vms": sum(1 for w in workloads if w.type == "vm"),
            "containers": sum(1 for w in workloads if w.type == "lxc"),
            "storage": len(storage),
        },
        "cluster_status": cluster_status,
        "nodes_online": nodes_online,
        "nodes_total": len(nodes),
        "workloads_running": workloads_running,
        "workloads_total": len(workloads),
        "storage_used_pct": storage_pct,
        "storage_used_bytes": total_used if total_capacity else None,
        "storage_total_bytes": total_capacity or None,
        "mem_used_bytes": round(mem_used) if mem_total else None,
        "mem_total_bytes": mem_total or None,
        "nodes_with_updates": nodes_with_updates,
        "nodes_in_maintenance": nodes_in_maintenance,
        "operations_awaiting_approval": operations_awaiting_approval,
        "failed_tasks": failed_tasks or 0,
        "nodes_detail": [
            {
                "id": str(n.id),
                "name": n.name,
                "status": n.status,
                "cpu_usage_pct": n.cpu_usage_pct,
                "mem_usage_pct": n.mem_usage_pct,
                "pending_updates": n.pending_updates,
                "maintenance_mode": n.maintenance_mode,
            }
            for n in sorted(nodes, key=lambda x: x.name)
        ],
    }



@router.get("/settings", response_model=schemas.AppSettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return db.query(AppSettings).filter(AppSettings.id == 1).one()


def _settings_snapshot(row: AppSettings) -> dict:
    # Never include smtp_encrypted_password (or the plaintext write-only
    # smtp_password field) here -- audit.py's own docstring is explicit
    # that callers must redact secret material before writing an event.
    # smtp_password_set (a bool) is the safe stand-in.
    return {
        "inventory_refresh_interval_seconds": row.inventory_refresh_interval_seconds,
        "tls_verify_default": row.tls_verify_default,
        "timezone": row.timezone,
        "rightsizing_cpu_peak_target_pct": row.rightsizing_cpu_peak_target_pct,
        "rightsizing_mem_peak_target_pct": row.rightsizing_mem_peak_target_pct,
        "rightsizing_round_vcpu_even": row.rightsizing_round_vcpu_even,
        "pve_mutations_enabled": row.pve_mutations_enabled,
        "console_enabled": row.console_enabled,
        "smtp_enabled": row.smtp_enabled,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_username": row.smtp_username,
        "smtp_from_address": row.smtp_from_address,
        "smtp_use_tls": row.smtp_use_tls,
        "smtp_password_set": row.smtp_password_set,
        "notification_recipient": row.notification_recipient,
        "notification_hold_down_minutes": row.notification_hold_down_minutes,
    }


@router.put("/settings", response_model=schemas.AppSettingsOut, dependencies=[Depends(require_admin)])
def update_settings(payload: schemas.AppSettingsUpdate, db: Session = Depends(get_db)):
    from pyxie_core.audit import write_audit_event
    from pyxie_core.crypto import encrypt_secret

    row = db.query(AppSettings).filter(AppSettings.id == 1).one()
    before = _settings_snapshot(row)
    updates = payload.model_dump(exclude_unset=True)
    smtp_password = updates.pop("smtp_password", None)
    for field, value in updates.items():
        setattr(row, field, value)
    if smtp_password:
        row.smtp_encrypted_password = encrypt_secret(smtp_password)
    db.commit()
    db.refresh(row)
    after = _settings_snapshot(row)
    write_audit_event(
        db,
        event_category="settings",
        event_type="settings.changed",
        actor="user",
        actor_type="user",
        state_before=before,
        state_after=after,
    )
    # The global write kill switch deserves its own loud, easy-to-find
    # audit entry -- not buried in a routine settings.changed event that
    # could just as easily be a timezone tweak. Separate from the write
    # path's own re-check at call time; this only records the toggle
    # itself.
    if before["console_enabled"] != after["console_enabled"]:
        write_audit_event(
            db,
            event_category="settings",
            event_type="settings.console_enabled_changed",
            actor="user",
            actor_type="user",
            result="success",
            severity="warning" if after["console_enabled"] else "info",
            state_before={"console_enabled": before["console_enabled"]},
            state_after={"console_enabled": after["console_enabled"]},
        )
    if before["pve_mutations_enabled"] != after["pve_mutations_enabled"]:
        write_audit_event(
            db,
            event_category="settings",
            event_type="settings.pve_mutations_enabled_changed",
            actor="user",
            actor_type="user",
            result="success",
            severity="warning" if after["pve_mutations_enabled"] else "info",
            state_before={"pve_mutations_enabled": before["pve_mutations_enabled"]},
            state_after={"pve_mutations_enabled": after["pve_mutations_enabled"]},
        )
    return row


@router.post("/settings/test-email", dependencies=[Depends(require_admin)])
def test_email(db: Session = Depends(get_db)):
    """Send one real test email to the saved notification recipient using
    the saved SMTP settings, synchronously -- same shape as the PVE
    credential test-connection endpoints (no RQ job for a sub-second SMTP
    handshake). Always operates on what's currently saved in the
    database, not any unsaved edits still sitting in the settings form."""
    from pyxie_core.audit import write_audit_event
    from pyxie_core.mail import send_email

    row = db.query(AppSettings).filter(AppSettings.id == 1).one()
    if not row.notification_recipient:
        return {"status": "failed", "error": "set a notification recipient address first"}

    try:
        send_email(
            row,
            row.notification_recipient,
            subject="PyXie test notification",
            body="This is a test email from PyXie Manager to confirm your SMTP settings are working.",
        )
    except Exception as e:
        write_audit_event(
            db,
            event_category="settings",
            event_type="settings.smtp_test_email_sent",
            actor="user",
            actor_type="user",
            result="failure",
            severity="warning",
            error=str(e),
            metadata={"recipient": row.notification_recipient},
        )
        return {"status": "failed", "error": str(e)}

    write_audit_event(
        db,
        event_category="settings",
        event_type="settings.smtp_test_email_sent",
        actor="user",
        actor_type="user",
        result="success",
        metadata={"recipient": row.notification_recipient},
    )
    return {"status": "ok"}
