"""pbs.storage_add: add a Proxmox Backup Server datastore to Proxmox VE as a backup storage, so guests can be restored from it.

Proxmox restores only from storages it has. A datastore that only the backup server knows (for example the offsite copy) is
listed by PyXie but cannot be restored from until Proxmox has a storage for it. This adds that storage with a dedicated
read-only "restore" token (DatastoreReader), never the admin token.

Safety Contract: preview (the restore token can read the datastore, the backup server's certificate fingerprint is read, the
storage ID is free) -> approve -> revalidate -> lock -> one synchronous Proxmox call -> read back and check the storage is active -> audit.
The token secret is read from PyXie's encrypted store at execution time and is never kept in the operation.
"""

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import rollback
from .audit import write_audit_event
from .credentials import load_pve_credentials
from .crypto import decrypt_secret
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Cluster, Node, Operation, ProtectionCredential, ProtectionTarget, PveTarget
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pbs_client import PbsClient
from .pbs_server import PbsNotConfigured, load_pbs_credentials
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient

now = lambda: datetime.now(timezone.utc)
STORAGE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,39}$")


def default_storage_id(datastore: str, namespace: str = "", purpose: str = "restore") -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "-", datastore + ("-" + namespace if namespace else "")).strip("-._")
    return (("pbs-" if purpose == "restore" else "pbsb-") + base)[:40]


def dry_run_pbs_storage_add(db: Session, pbs: ProtectionTarget, cluster: Cluster, *, datastore: str, storage_id: str | None, actor: str, namespace: str = "", purpose: str = "restore") -> Operation:
    namespace = (namespace or "").strip().strip("/")
    purpose = "backup" if purpose == "backup" else "restore"
    sid = (storage_id or default_storage_id(datastore, namespace, purpose)).strip()
    ctx = {"protection_target_id": str(pbs.id), "target": pbs.name, "node": pbs.name, "datastore": datastore, "storage_id": sid, "namespace": namespace, "purpose": purpose}
    op = create_operation(db, "pbs.storage_add", cluster_id=cluster.id, created_by=actor, context=ctx)
    reasons: list[str] = []
    rules: list[str] = []
    fp = None
    if not STORAGE_ID_RE.match(sid):
        reasons.append("the storage ID must start with a letter and use letters, digits, dot, dash or underscore")
        rules.append("SAFE-PBSSTORE-001")
    try:
        rcreds = load_pbs_credentials(db, pbs, purpose)
        with PbsClient(rcreds) as rc:
            stores = {d["store"] for d in (rc.datastores() or [])}
            if datastore not in stores:
                reasons.append(f"the {purpose} token cannot see datastore {datastore}: give it the {'DatastoreReader' if purpose == 'restore' else 'DatastorePowerUser'} role on it")
                rules.append("SAFE-PBSSTORE-001")
            else:
                rc._get(f"/admin/datastore/{datastore}/groups", {"ns": namespace} if namespace else None)
    except PbsNotConfigured:
        reasons.append(f"no {purpose} token is saved for this backup server: create the {'read-only restore' if purpose == 'restore' else 'backup'} account (Settings, Credentials, Proxmox Backup Server accounts) and add its token there")
        rules.append("SAFE-PBSSTORE-001")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"the {purpose} token could not read {datastore}: {str(exc)[:140]}")
        rules.append("SAFE-PBSSTORE-001")
    if not rules:
        try:
            try:
                ic = PbsClient(load_pbs_credentials(db, pbs, "administrative"))
            except PbsNotConfigured:
                ic = PbsClient(load_pbs_credentials(db, pbs, "inventory"))
            with ic:
                certs = ic._get("/nodes/localhost/certificates/info") or []
            cert = next((c for c in certs if c.get("filename") == "proxy.pem"), certs[0] if certs else None)
            fp = (cert or {}).get("fingerprint")
            if not fp:
                raise ValueError("no certificate fingerprint was returned")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not read the backup server's certificate fingerprint: {str(exc)[:120]}")
            rules.append("SAFE-PBSSTORE-001")
    if not rules:
        try:
            target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
            client, _c = build_pve_client(db, target)
            with client, PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as adm:
                existing = {s.get("storage") for s in (adm._get("/storage") or [])}
            if sid in existing:
                reasons.append(f"a Proxmox storage named {sid} already exists")
                rules.append("SAFE-PBSSTORE-001")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not read the Proxmox storage list: {str(exc)[:120]}")
            rules.append("SAFE-PBSSTORE-001")
    if not rules:
        reasons.insert(0, f"Add datastore {datastore}{'/' + namespace if namespace else ''} of {pbs.name} to Proxmox as backup storage '{sid}' (server {pbs.hostname}, certificate {fp[:23]}…), so guests can be restored from it.")
        if purpose == "backup":
            reasons[0] = reasons[0].replace("so guests can be restored from it.", "so a backup policy can back guests up into it and restore from it.")
            reasons.append("Proxmox keeps the backup token's secret in its own storage configuration (cluster-wide). The token can write and prune only the backups it owns (DatastorePowerUser). Remove the storage later in Storage setup; nothing on the backup server changes.")
        else:
            reasons.append("Proxmox keeps the read-only restore token's secret in its own storage configuration (cluster-wide). The storage only reads: it can restore, not write or delete backups. Remove the storage later in Storage setup; nothing on the backup server changes.")
    result = {"target": pbs.name, "node": pbs.name, "datastore": datastore, "namespace": namespace, "storage_id": sid, "eligible": not rules, "reasons": reasons, "blocking_safety_rules": sorted(set(rules))}
    if rules:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(rules)), actor=actor)
    op.context = {**ctx, "fingerprint": fp}
    db.commit()
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_pbs_storage_add(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise OperationError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    pbs = db.query(ProtectionTarget).filter(ProtectionTarget.id == ctx["protection_target_id"]).one()
    sid, ds = ctx["storage_id"], ctx["datastore"]
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as adm:
                if sid in {s.get("storage") for s in (adm._get("/storage") or [])}:
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"a storage named {sid} appeared since the preview; nothing was added. Preview again")
            try:
                acquire_lock(db, resource_type="cluster", resource_id=cluster.id, operation_id=op.id, reason="pbs.storage_add", ttl_seconds=600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"])
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing" and not op.pve_task_result:
            cred = db.query(ProtectionCredential).filter(ProtectionCredential.protection_target_id == pbs.id, ProtectionCredential.slot_name == (ctx.get("purpose") or "restore")).one()
            spec = {"id": sid, "server": pbs.hostname, "port": pbs.api_port, "datastore": ds, "username": f"{cred.token_user}!{cred.token_id}",
                    "fingerprint": ctx["fingerprint"], "namespace": ctx.get("namespace") or ""}
            try:
                with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                    wc.create_pbs_storage(spec, decrypt_secret(cred.encrypted_secret))
            except MutationsDisabledError as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"Proxmox refused the storage: {str(exc)[:200]}")
            op.pve_task_result = {"created": True}
            db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            release_locks_for_operation(db, op.id)
            problems: list[str] = []
            rc, _c = build_pve_client(db, target)
            with rc, PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as adm:
                got = adm._get(f"/storage/{sid}") or {}
                if got.get("type") != "pbs" or got.get("datastore") != ds or (got.get("namespace") or "") != (ctx.get("namespace") or ""):
                    problems.append("the storage does not read back as a backup server datastore")
                node = next((n.name for n in db.query(Node).filter(Node.cluster_id == cluster.id, Node.status == "online", Node.is_missing.is_(False)).order_by(Node.name)), None)
                active = next((s for s in (rc.storage_list(node) or []) if s.get("storage") == sid), None) if node else None
                if node and (active is None or not active.get("active", 1)):
                    problems.append(f"Proxmox lists the storage but it is not active on {node} (the token or fingerprint may be wrong)")
            op.verification_result = {"storage": sid, "datastore": ds, "problems": problems}
            db.commit()
            write_audit_event(db, event_category="protection", event_type="pbs.storage_added", actor=op.approved_by or "system", actor_type="user",
                              metadata={"storage": sid, "datastore": ds, "server": pbs.hostname})
            if problems:
                return fail_operation(db, op, error="; ".join(problems) + f". The storage '{sid}' exists: remove it in Storage setup if it is wrong")
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
