"""Proxmox Backup Server as a managed host: setup and access, status, jobs, patching."""

import base64
import hashlib
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core import host_kit as hk
from pyxie_core import pbs_jobs
from pyxie_core.audit import write_audit_event
from pyxie_core.crypto import decrypt_secret, encrypt_secret, mask_secret
from pyxie_core.host_maintenance_client import HostMaintenanceConnectionError, derive_public_key_line, probe_host_key
from pyxie_core.models import HostMaintenanceCredential, Operation, ProtectionCredential, ProtectionTarget
from pyxie_core.pbs_client import PbsClient
from pyxie_core.pbs_datastore_workflow import dry_run_datastore_change
from pyxie_core.pbs_job_workflow import dry_run_job_change
from pyxie_core.pbs_storage_workflow import dry_run_pbs_storage_add
from pyxie_core.pbs_server import PbsNotConfigured, check_ssh, load_pbs_credentials, refresh_status
from pyxie_core.pbs_maintenance_workflow import dry_run_pbs_maintenance
from pyxie_core.pbs_reboot_workflow import dry_run_pbs_reboot
from pyxie_core.pbs_update_workflow import dry_run_pbs_update

from .. import config
from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .host_kit import KIT_DIR, _r

router = APIRouter(prefix="/api/pbs", tags=["pbs"], dependencies=[Depends(get_current_user)])
SLOTS = ("inventory", "administrative", "restore", "backup")


def _target(db: Session, target_id) -> ProtectionTarget:
    t = db.query(ProtectionTarget).filter(ProtectionTarget.id == target_id).one_or_none()
    if t is None:
        raise HTTPException(404, "backup server not found")
    return t


def _slots(db: Session, t: ProtectionTarget) -> dict:
    rows = db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == t.id).all()
    return {r.slot_name: {"id": str(r.id), "slot_name": r.slot_name, "token_user": r.token_user, "token_id": r.token_id,
                          "token": f"{r.token_user}!{r.token_id}", "masked_secret": mask_secret(decrypt_secret(r.encrypted_secret)),
                          "status": r.status, "last_validated_at": r.last_validated_at.isoformat() if r.last_validated_at else None} for r in rows}


def _ssh(db: Session, t: ProtectionTarget) -> dict:
    c = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.protection_target_id == t.id).one_or_none()
    return {"generated": c is not None, "username": c.ssh_username if c else None, "status": c.status if c else None,
            "host_key_pinned": bool(t.ssh_host_key_base64), "fingerprint": t.ssh_host_key_fingerprint,
            "wrapper_version": t.wrapper_version, "expected_wrapper_version": hk.wrapper_version(KIT_DIR),
            "wrapper_outdated": hk.is_outdated(t.wrapper_version, hk.wrapper_version(KIT_DIR))}


def _summary(db: Session, t: ProtectionTarget) -> dict:
    return {"id": str(t.id), "name": t.name, "hostname": t.hostname, "api_port": t.api_port, "tls_verify": t.tls_verify,
            "status": t.host_status, "status_at": t.host_status_at.isoformat() if t.host_status_at else None,
            "credentials": _slots(db, t), "ssh": _ssh(db, t)}


@router.get("/servers")
def list_servers(db: Session = Depends(get_db)):
    return [_summary(db, t) for t in db.query(ProtectionTarget).order_by(ProtectionTarget.name).all()]


@router.get("/namespaces")
def namespaces_overview(db: Session = Depends(get_db)):
    """Every backup server's datastores with their namespaces: how many guests and backups each holds, and what uses it (policy copies and PBS sync/prune jobs). Read-only."""
    from pyxie_core.models import BackupPolicy
    pols = db.query(BackupPolicy).all()
    out = []
    for t in db.query(ProtectionTarget).order_by(ProtectionTarget.name).all():
        item = {"id": str(t.id), "name": t.name, "can_write": False, "error": None, "stores": []}
        try:
            slot = "administrative" if db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == t.id, ProtectionCredential.slot_name == "administrative").count() else "inventory"
            item["can_write"] = slot == "administrative"
            with PbsClient(load_pbs_credentials(db, t, slot)) as c:
                cfg = {d["name"]: d for d in (c._get("/config/datastore") or [])}
                jobs = [("sync", j) for j in (c.job_config("sync") or [])] + [("prune", j) for j in (c.job_config("prune") or [])]
                for d in c.datastores() or []:
                    n = d["store"]
                    rows = []
                    try:
                        nss = [x.get("ns", "") for x in (c._get(f"/admin/datastore/{n}/namespace") or [])]
                    except Exception:  # noqa: BLE001
                        nss = [""]
                    for ns in nss:
                        try:
                            gs = c._get(f"/admin/datastore/{n}/groups", {"ns": ns}) if ns else (c.groups(n) or [])
                        except Exception:  # noqa: BLE001
                            gs = []
                        used = [f"policy {p.name}: {ly.get('label')}" for p in pols for ly in (p.layers or []) if ly.get("target_id") == str(t.id) and ly.get("store") == n and (ly.get("namespace") or "") == ns]
                        used += [f"{k} job {j['id']}" for k, j in jobs if j.get("store") == n and (j.get("ns") or "") == ns and not (ns == "" and k == "sync" and False)]
                        rows.append({"ns": ns, "guests": len(gs), "backups": sum(int(g.get("backup-count") or 0) for g in gs), "used_by": used, "children": sum(1 for x in nss if x.startswith(ns + "/")) if ns else sum(1 for x in nss if x)})
                    s3 = (d.get("backend-type") == "s3") or ("s3" in str(cfg.get(n, {}).get("backend", "")))
                    item["stores"].append({"name": n, "backend": "s3" if s3 else "filesystem", "gc_schedule": cfg.get(n, {}).get("gc-schedule"), "namespaces": rows})
        except Exception as exc:  # noqa: BLE001
            item["error"] = f"could not read this backup server: {str(exc)[:160]}"
        out.append(item)
    return out


@router.get("/servers/{target_id}")
def server_detail(target_id: uuid.UUID, db: Session = Depends(get_db)):
    t = _target(db, target_id)
    out = _summary(db, t)
    out["jobs"], out["stores"], out["remotes"], out["error"], out["datastores"] = {}, [], [], None, []
    try:
        slot = "administrative" if "administrative" in out["credentials"] else "inventory"
        with PbsClient(load_pbs_credentials(db, t, slot)) as c:
            out["stores"] = [d["store"] for d in (c.datastores() or [])]
            out["remotes"] = [r.get("name") for r in c.remotes()]
            for kind in pbs_jobs.FIELDS:
                state = {j["id"]: j for j in c.job_state(kind)}
                out["jobs"][kind] = [{"id": j["id"], "ns": j.get("ns") or "", "store": j.get("store"), "schedule": j.get("schedule"), "settings": pbs_jobs.normalize(j, kind),
                                      "summary": pbs_jobs.describe(kind, j), "last_state": state.get(j["id"], {}).get("last-run-state"),
                                      "last_end": state.get(j["id"], {}).get("last-run-endtime"), "next": state.get(j["id"], {}).get("next-run")}
                                     for j in c.job_config(kind)]
            cfg = {d["name"]: d for d in (c._get("/config/datastore") or [])}
            usage = {u.get("store"): u for u in (c.datastore_usage() or [])}
            for name in out["stores"]:
                try:
                    gc = c._get(f"/admin/datastore/{name}/gc") or {}
                except Exception:  # noqa: BLE001
                    gc = {}
                u = usage.get(name, {})
                out["datastores"].append({"name": name, "backend": "s3" if cfg.get(name, {}).get("backend") else "filesystem", "gc_schedule": cfg.get(name, {}).get("gc-schedule"),
                                          "gc": {"last_state": gc.get("last-run-state"), "last_end": gc.get("last-run-endtime"), "next": gc.get("next-run"),
                                                 "removed_bytes": gc.get("removed-bytes"), "pending_bytes": gc.get("pending-bytes")}, "used": u.get("used"), "total": u.get("total")})
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"could not read jobs: {exc}"
    return out


@router.post("/servers/{target_id}/refresh", dependencies=[Depends(require_admin)])
def refresh(target_id: uuid.UUID, db: Session = Depends(get_db)):
    return refresh_status(db, _target(db, target_id))


class TokenIn(BaseModel):
    token_user: str
    token_id: str
    token_secret: str = ""  # empty on an edit keeps the saved secret


@router.put("/servers/{target_id}/credentials/{slot}", dependencies=[Depends(require_admin)])
def save_token(target_id: uuid.UUID, slot: str, payload: TokenIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if slot not in SLOTS:
        raise HTTPException(400, "slot must be inventory or administrative")
    t = _target(db, target_id)
    row = db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == t.id, ProtectionCredential.slot_name == slot).one_or_none()
    if row is None and not payload.token_secret:
        raise HTTPException(400, "a token secret is required for a new token")
    if row is None:
        row = ProtectionCredential(protection_target_id=t.id, slot_name=slot, token_user=payload.token_user, token_id=payload.token_id,
                                   encrypted_secret=encrypt_secret(payload.token_secret), status="untested")
        db.add(row)
    else:
        row.token_user, row.token_id = payload.token_user, payload.token_id
        if payload.token_secret:
            row.encrypted_secret = encrypt_secret(payload.token_secret)
        row.status, row.last_validated_at = "untested", None
    write_audit_event(db, event_category="credential", event_type="pbs_credential.set", actor=user.email, actor_type="user",
                      provider_id=t.provider_id, metadata={"target": t.name, "slot": slot, "token": f"{payload.token_user}!{payload.token_id}"})
    db.commit()
    return {"saved": True, "slot": slot}


@router.get("/servers/{target_id}/credentials")
def list_tokens(target_id: uuid.UUID, db: Session = Depends(get_db)):
    return _slots(db, _target(db, target_id))


@router.delete("/servers/{target_id}/credentials/{slot}", dependencies=[Depends(require_admin)])
def delete_token(target_id: uuid.UUID, slot: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if slot not in SLOTS:
        raise HTTPException(400, "slot must be inventory or administrative")
    if slot == "inventory":
        raise HTTPException(409, "the read token is what PyXie watches this server with; replace it with Edit instead of deleting it")
    t = _target(db, target_id)
    row = db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == t.id, ProtectionCredential.slot_name == slot).one_or_none()
    if row is None:
        raise HTTPException(404, "no such token")
    write_audit_event(db, event_category="credential", event_type="pbs_credential.deleted", actor=user.email, actor_type="user",
                      provider_id=t.provider_id, metadata={"target": t.name, "slot": slot})
    db.delete(row)
    db.commit()
    return {"deleted": True}


def _probe(label: str, fn) -> dict:
    try:
        v = fn()
        return {"check": label, "ok": True, "detail": v}
    except Exception as exc:  # noqa: BLE001
        return {"check": label, "ok": False, "detail": str(exc)[:160]}


@router.post("/servers/{target_id}/test-access", dependencies=[Depends(require_admin)])
def test_access(target_id: uuid.UUID, slot: str = "inventory", db: Session = Depends(get_db)):
    """What this token can actually see. A job list that is empty for a token that lacks permission looks the same as
    'no jobs', so the check compares what PBS lists with what the token's permissions allow."""
    t = _target(db, target_id)
    if slot not in SLOTS:
        raise HTTPException(400, "slot must be inventory or administrative")
    try:
        creds = load_pbs_credentials(db, t, slot)
    except PbsNotConfigured as exc:
        raise HTTPException(409, str(exc))
    checks = []
    if slot in ("restore", "backup"):
        need = "Datastore.Read" if slot == "restore" else "Datastore.Backup"
        with PbsClient(creds) as c:
            checks.append(_probe("Datastores it can read", lambda: ", ".join(d["store"] for d in (c.datastores() or [])) or "none"))
            perms = _probe("Permissions", lambda: c._get("/access/permissions"))
            if perms["ok"] and isinstance(perms["detail"], dict):
                privs = sorted({x for v in perms["detail"].values() if isinstance(v, dict) for x in v})
                perms["detail"] = ", ".join(privs) or "none"
                checks.append({"check": ("Can read backup data (Datastore.Read, needed to restore)" if slot == "restore" else "Can write backups (Datastore.Backup, needed to back guests up)"), "ok": need in privs,
                               "detail": "yes" if need in privs else ("no: give the user and the token the DatastoreReader role on /datastore" if slot == "restore" else "no: give the user and the token the DatastorePowerUser role on /datastore")})
            checks.append(perms)
        row = db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == t.id, ProtectionCredential.slot_name == slot).one()
        row.status = "valid" if all(x["ok"] for x in checks) else "invalid"
        row.last_validated_at = datetime.now(timezone.utc)
        db.commit()
        return {"slot": slot, "ok": all(x["ok"] for x in checks), "checks": checks}
    with PbsClient(creds) as c:
        checks.append(_probe("Server version", lambda: (c.version() or {}).get("version")))
        checks.append(_probe("Datastores", lambda: f"{len(c.datastores() or [])} visible"))
        for kind in pbs_jobs.FIELDS:
            checks.append(_probe(f"{kind.capitalize()} jobs", lambda k=kind: f"{len(c.job_config(k))} visible"))
        checks.append(_probe("Remotes (needed to see and edit sync jobs)", lambda: f"{len(c.remotes())} visible"))
        checks.append(_probe("Server status", lambda: "readable" if c.node_status() else "empty"))
        checks.append(_probe("Package updates", lambda: f"{len(c.apt_updates())} pending"))
        checks.append(_probe("Tasks", lambda: f"{len(c.tasks(limit=5))} readable"))
        perms = _probe("Permissions", lambda: c._get("/access/permissions"))
        if perms["ok"] and isinstance(perms["detail"], dict):
            p = perms["detail"]
            privs = sorted({x for v in p.values() if isinstance(v, dict) for x in v})
            perms["detail"] = ", ".join(privs[:14]) + ("…" if len(privs) > 14 else "") or "none"
            if slot == "administrative":
                checks.append({"check": "Can reboot the server (needs Sys.PowerManagement)", "ok": "Sys.PowerManagement" in privs,
                               "detail": "yes" if "Sys.PowerManagement" in privs else "no: needed only for Reboot Node and Full Maintenance; grant the Admin role on /system to the user and the token"})
            checks.append({"check": "Can list sync jobs and remotes (needs Remote.Audit)", "ok": "Remote.Audit" in privs or "Remote.Read" in privs,
                           "detail": "yes" if ("Remote.Audit" in privs or "Remote.Read" in privs) else "no: give the token the Audit role on / so PyXie can see sync jobs"})
        checks.append(perms)
    ok = all(x["ok"] for x in checks)
    row = db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == t.id, ProtectionCredential.slot_name == slot).one()
    row.status = "valid" if checks[0]["ok"] else "invalid"
    row.last_validated_at = datetime.now(timezone.utc)
    db.commit()
    return {"slot": slot, "ok": ok, "checks": checks}


@router.post("/servers/{target_id}/ssh/generate", dependencies=[Depends(require_admin)])
def generate_ssh(target_id: uuid.UUID, force: bool = False, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    existing = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.protection_target_id == t.id).one_or_none()
    if existing is not None and not force:
        raise HTTPException(409, "an SSH key already exists for this server; regenerating it means re-running the host kit on the PBS")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()
    if existing is None:
        db.add(HostMaintenanceCredential(protection_target_id=t.id, ssh_username="pyxie-hostmaint", ssh_port=22,
                                         encrypted_private_key=encrypt_secret(pem), status="untested", created_by=user.email))
    else:
        existing.encrypted_private_key = encrypt_secret(pem)
        existing.status, existing.last_validated_at = "untested", None
    write_audit_event(db, event_category="credential", event_type="pbs_ssh_credential.generated", actor=user.email, actor_type="user",
                      provider_id=t.provider_id, metadata={"target": t.name, "regenerated": existing is not None})
    db.commit()
    return _ssh(db, t)


@router.post("/servers/{target_id}/ssh/check", dependencies=[Depends(require_admin)])
def ssh_check(target_id: uuid.UUID, db: Session = Depends(get_db)):
    """Read-only: connect with the pinned key and ask the wrapper for version and status."""
    return check_ssh(db, _target(db, target_id))


@router.post("/servers/{target_id}/host-kit/link", dependencies=[Depends(require_admin)])
def kit_link(target_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    c = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.protection_target_id == t.id).one_or_none()
    if c is None:
        raise HTTPException(409, "generate the SSH key first")
    from pyxie_core.crypto import decrypt_secret
    data = hk.build_installer(KIT_DIR, derive_public_key_line(decrypt_secret(c.encrypted_private_key)), t.name, config.settings.APP_VERSION)
    token = hk.new_link_token()
    hk.store_link(_r(), token, str(t.id))
    write_audit_event(db, event_category="credential", event_type="host_kit.link_created", actor=user.email, actor_type="user",
                      provider_id=t.provider_id, metadata={"protection_target_id": str(t.id), "sha256": hk.sha256_hex(data)})
    return {"path": f"/host-kit/{token}", "sha256": hk.sha256_hex(data), "size": len(data), "expires_in": hk.LINK_TTL_SECONDS,
            "kit_version": config.settings.APP_VERSION, "wrapper_version": hk.wrapper_version(KIT_DIR)}


@router.post("/servers/{target_id}/ssh/probe", dependencies=[Depends(require_admin)])
def probe_key(target_id: uuid.UUID, db: Session = Depends(get_db)):
    t = _target(db, target_id)
    try:
        key_type, key_b64, fp = probe_host_key(t.hostname)
    except HostMaintenanceConnectionError as exc:
        raise HTTPException(502, f"could not reach {t.hostname} on SSH: {exc}")
    return {"key_type": key_type, "key_base64": key_b64, "fingerprint": fp}


class PinIn(BaseModel):
    key_type: str
    key_base64: str


@router.put("/servers/{target_id}/ssh-host-key", dependencies=[Depends(require_admin)])
def pin_key(target_id: uuid.UUID, payload: PinIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    t.ssh_host_key_type, t.ssh_host_key_base64 = payload.key_type, payload.key_base64
    t.ssh_host_key_fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(payload.key_base64)).digest()).decode().rstrip("=")
    t.ssh_host_key_pinned_at, t.ssh_host_key_pinned_by = datetime.now(timezone.utc), user.email
    write_audit_event(db, event_category="credential", event_type="pbs.ssh_host_key_pinned", actor=user.email, actor_type="user",
                      provider_id=t.provider_id, metadata={"target": t.name, "fingerprint": t.ssh_host_key_fingerprint})
    db.commit()
    return {"pinned": True, "fingerprint": t.ssh_host_key_fingerprint}


@router.delete("/servers/{target_id}/ssh-host-key", dependencies=[Depends(require_admin)])
def unpin_key(target_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    t.ssh_host_key_type = t.ssh_host_key_base64 = t.ssh_host_key_fingerprint = None
    t.ssh_host_key_pinned_at = t.ssh_host_key_pinned_by = None
    write_audit_event(db, event_category="credential", event_type="pbs.ssh_host_key_cleared", actor=user.email, actor_type="user",
                      provider_id=t.provider_id, metadata={"target": t.name})
    db.commit()
    return {"pinned": False}


class JobChange(BaseModel):
    kind: str
    action: str
    job_id: str
    params: dict = {}


def _existing_live(db: Session, op_type: str, target_id) -> Operation | None:
    for o in db.query(Operation).filter(Operation.operation_type_id == op_type,
                                        Operation.status.in_(["pending", "dry_run", "awaiting_approval", "approved", "revalidating", "executing", "verifying"])).all():
        if (o.context or {}).get("protection_target_id") == str(target_id):
            return o
    return None


@router.post("/servers/{target_id}/jobs/dry-run", dependencies=[Depends(require_admin)])
def job_dry_run(target_id: uuid.UUID, payload: JobChange, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    live = _existing_live(db, "pbs.job_change", t.id)
    if live is not None:
        raise HTTPException(409, "another PBS job change is already open for this server; confirm or cancel it first")
    op = dry_run_job_change(db, t, kind=payload.kind, action=payload.action, job_id=payload.job_id, params=payload.params, actor=user.email)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


@router.post("/servers/{target_id}/reboot/dry-run", dependencies=[Depends(require_admin)])
def reboot_dry_run(target_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    if _existing_live(db, "pbs.reboot", t.id) is not None or _existing_live(db, "pbs.maintenance", t.id) is not None:
        raise HTTPException(409, "a reboot or maintenance run is already open for this server")
    op = dry_run_pbs_reboot(db, t, actor=user.email)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


@router.post("/servers/{target_id}/maintenance/dry-run", dependencies=[Depends(require_admin)])
def maintenance_dry_run(target_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    for typ in ("pbs.reboot", "pbs.maintenance", "pbs.update"):
        if _existing_live(db, typ, t.id) is not None:
            raise HTTPException(409, "an update, reboot or maintenance run is already open for this server")
    op = dry_run_pbs_maintenance(db, t, actor=user.email)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


class DatastoreChange(BaseModel):
    store: str
    action: str
    params: dict = {}


@router.post("/servers/{target_id}/datastores/dry-run", dependencies=[Depends(require_admin)])
def datastore_dry_run(target_id: uuid.UUID, payload: DatastoreChange, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    if _existing_live(db, "pbs.datastore_change", t.id) is not None:
        raise HTTPException(409, "another datastore change is already open for this server; confirm or cancel it first")
    op = dry_run_datastore_change(db, t, store=payload.store, action=payload.action, params=payload.params, actor=user.email)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


class StorageAdd(BaseModel):
    workload_id: uuid.UUID | None = None
    cluster_id: uuid.UUID | None = None
    purpose: str = "restore"
    datastore: str
    storage_id: str | None = None
    namespace: str = ""


@router.post("/servers/{target_id}/storage-add/dry-run", dependencies=[Depends(require_admin)])
def storage_add_dry_run(target_id: uuid.UUID, payload: StorageAdd, user=Depends(get_current_user), db: Session = Depends(get_db)):
    from pyxie_core.models import Cluster, Workload
    t = _target(db, target_id)
    if payload.purpose not in ("restore", "backup"):
        raise HTTPException(400, "purpose must be restore or backup")
    cluster = None
    if payload.workload_id:
        w = db.query(Workload).filter(Workload.id == payload.workload_id).one_or_none()
        if w is None:
            raise HTTPException(404, "guest not found")
        cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one()
    elif payload.cluster_id:
        cluster = db.query(Cluster).filter(Cluster.id == payload.cluster_id).one_or_none()
    else:
        cluster = db.query(Cluster).filter(Cluster.is_missing.is_(False)).order_by(Cluster.name).first()
    if cluster is None:
        raise HTTPException(404, "cluster not found")
    if _existing_live(db, "pbs.storage_add", t.id) is not None:
        raise HTTPException(409, "adding a datastore to Proxmox is already open for this server")
    op = dry_run_pbs_storage_add(db, t, cluster, datastore=payload.datastore, storage_id=payload.storage_id, actor=user.email, namespace=payload.namespace, purpose=payload.purpose)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


class UpdateIn(BaseModel):
    check_only: bool = False


@router.post("/servers/{target_id}/update/dry-run", dependencies=[Depends(require_admin)])
def update_dry_run(target_id: uuid.UUID, payload: UpdateIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    t = _target(db, target_id)
    live = _existing_live(db, "pbs.update", t.id)
    if live is not None:
        raise HTTPException(409, "a PBS update is already open for this server")
    op = dry_run_pbs_update(db, t, actor=user.email, check_only=payload.check_only)
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}
