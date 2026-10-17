"""pbs.datastore_change: garbage collection schedule and run, verify now, and protect or unprotect a backup, on a Proxmox Backup Server.

Safety Contract: preview (what exists now and exactly what changes, the datastore is not busy for runs that conflict) -> approve ->
revalidate (unchanged since the preview) -> lock the server -> write through the PBS API -> read back (settings) or return the
task id (runs, which can take hours) -> audit. Nothing here deletes a backup: garbage collection only frees chunks no backup uses.
"""

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import rollback
from .audit import write_audit_event
from .host_maintenance_client import MutationsDisabledError
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import Operation, ProtectionTarget
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pbs_client import PbsApiError, PbsClient, PbsWriteClient
from .pbs_server import PbsNotConfigured, load_pbs_credentials
from .policy_layers import valid_namespace

now = lambda: datetime.now(timezone.utc)
ACTIONS = ("gc_schedule", "gc_run", "verify_run", "protect", "ns_create", "ns_delete")
SCHEDULE_RE = re.compile(r"^[A-Za-z0-9 ,:*/.\-]{1,64}$")
STORE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def _read(db: Session, target: ProtectionTarget) -> PbsClient:
    try:
        return PbsClient(load_pbs_credentials(db, target, "administrative"))
    except PbsNotConfigured:
        return PbsClient(load_pbs_credentials(db, target, "inventory"))


def validate_snapshot(params: dict) -> tuple[dict, list[str]]:
    """Optional snapshot or group selection for verify/protect. Pure."""
    errors: list[str] = []
    out: dict = {}
    t, i, ts = params.get("backup_type"), params.get("backup_id"), params.get("backup_time")
    ns = str(params.get("ns") or "").strip().strip("/")
    if ns:
        if not valid_namespace(ns):
            errors.append("the namespace is not valid")
        out["ns"] = ns
    if t is not None:
        if t not in ("vm", "ct", "host"):
            errors.append("backup type must be vm, ct or host")
        out["backup-type"] = t
    if i is not None:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", str(i)):
            errors.append("backup id is not valid")
        out["backup-id"] = str(i)
    if ts is not None:
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 1:
            errors.append("backup time must be a unix time")
        out["backup-time"] = ts
    if ("backup-id" in out or "backup-time" in out) and "backup-type" not in out:
        errors.append("a backup id or time needs the backup type")
    if "backup-time" in out and "backup-id" not in out:
        errors.append("a backup time needs the backup id")
    return out, errors


def dry_run_datastore_change(db: Session, target: ProtectionTarget, *, store: str, action: str, params: dict, actor: str) -> Operation:
    op = create_operation(db, "pbs.datastore_change", created_by=actor,
                          context={"protection_target_id": str(target.id), "target": target.name, "node": target.name, "store": store, "action": action})
    reasons: list[str] = []
    rules: list[str] = []
    before = None
    clean: dict = {}
    snap: dict = {}

    def blocked():
        return enter_stage(db, op, status="blocked", stage="dry_run", blocking_safety_rules=sorted(set(rules)), actor=actor,
                           dry_run_result={"target": target.name, "node": target.name, "store": store, "action": action, "eligible": False, "reasons": reasons,
                                           "blocking_safety_rules": sorted(set(rules))})

    if action not in ACTIONS or not STORE_RE.match(store or ""):
        reasons.append("unknown action or datastore name")
        rules.append("SAFE-PBSJOB-001")
        return blocked()
    try:
        load_pbs_credentials(db, target, "administrative")
    except PbsNotConfigured:
        reasons.append("no write (administrative) API token is saved for this PBS: add one under Credentials")
        rules.append("SAFE-PBSJOB-002")
        return blocked()
    try:
        with _read(db, target) as c:
            stores = {d["name"]: d for d in (c._get("/config/datastore") or [])}
            running = c.tasks(running=True, limit=100)
            gc = c._get(f"/admin/datastore/{store}/gc") if store in stores else None
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"could not read the PBS server: {exc}")
        rules.append("SAFE-PBSJOB-002")
        return blocked()
    if store not in stores:
        reasons.append(f"datastore {store} does not exist on this PBS")
        rules.append("SAFE-PBSJOB-001")
        return blocked()
    busy_on_store = [t for t in running if store in str(t.get("worker_id") or "")]
    if action == "gc_schedule":
        sched = (params.get("schedule") or "").strip()
        before = stores[store].get("gc-schedule")
        if sched and not SCHEDULE_RE.match(sched):
            reasons.append("schedule has characters PBS calendar events never use")
            rules.append("SAFE-PBSJOB-001")
        elif (sched or None) == (before or None):
            reasons.append("nothing would change")
            rules.append("SAFE-PBSJOB-001")
        else:
            clean = {"gc-schedule": sched or None}
            reasons.append(f"Garbage collection of {store}: {before or '(no schedule)'} -> {sched or '(no schedule: it will only run when started by hand)'}. Garbage collection frees chunks no backup uses; it deletes no backup.")
    elif action == "gc_run":
        if any(t.get("worker_type") == "garbage_collection" and store in str(t.get("worker_id")) for t in busy_on_store):
            reasons.append("garbage collection is already running on this datastore")
            rules.append("SAFE-PBSJOB-001")
        else:
            reasons.append(f"Start garbage collection on {store} now. It frees chunks no backup uses and deletes no backup; it can take hours on a large or S3-backed datastore"
                           + (f" (last run: {gc.get('last-run-state')})" if gc and gc.get("last-run-state") else "") + ".")
    elif action == "verify_run":
        snap, errs = validate_snapshot(params)
        if errs:
            reasons.extend(errs)
            rules.append("SAFE-PBSJOB-001")
        else:
            what = "one backup" if "backup-time" in snap else ("every backup of one guest" if "backup-id" in snap else "EVERY backup on the datastore")
            reasons.append(f"Verify {what} on {store} now: PBS re-reads the data and checks it against its checksums; the result is stored as verified or failed. This reads a lot of data" + (" (and from S3, which counts as download)" if stores[store].get("backend") else "") + ".")
            clean = snap
    elif action == "protect":
        snap, errs = validate_snapshot(params)
        want = bool(params.get("protected", True))
        if errs or not all(k in snap for k in ("backup-type", "backup-id", "backup-time")):
            reasons.extend(errs or ["choose one backup (type, id and time)"])
            rules.append("SAFE-PBSJOB-001")
        else:
            clean = {**snap, "protected": want}
            reasons.append(("Protect" if want else "Remove protection from") + f" the backup on {store}: " + ("pruning and manual deletes will skip it." if want else "pruning can delete it again."))
    elif action in ("ns_create", "ns_delete"):
        ns = str(params.get("ns") or "").strip().strip("/")
        clean = {"ns": ns}
        if not ns or not valid_namespace(ns):
            reasons.append("a namespace name uses letters, digits and . _ - (up to 7 levels separated by /)")
            rules.append("SAFE-PBSJOB-001")
        else:
            try:
                with _read(db, target) as c:
                    have = [x.get("ns", "") for x in (c._get(f"/admin/datastore/{store}/namespace") or [])]
                    groups = c._get(f"/admin/datastore/{store}/groups", {"ns": ns}) if ns in have else []
                    jobs = [("sync", j) for j in c.job_config("sync")] + [("prune", j) for j in c.job_config("prune")]
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"could not read the namespaces: {str(exc)[:140]}")
                rules.append("SAFE-PBSJOB-002")
                have, groups, jobs = [], [], []
            if not rules and action == "ns_create":
                if ns in have:
                    reasons.append(f"the namespace {ns} already exists on {store}")
                    rules.append("SAFE-PBSJOB-001")
                else:
                    parent = "/".join(ns.split("/")[:-1])
                    if parent and parent not in have:
                        reasons.append(f"the parent namespace {parent} does not exist yet: create it first")
                        rules.append("SAFE-PBSJOB-001")
                    else:
                        reasons.append(f"Create the namespace {ns} in datastore {store}: a folder inside the datastore that shares its stored data (so nothing is stored twice) but has its own backups, jobs and retention. It costs no space and deletes nothing.")
            elif not rules:
                kids = [n for n in have if n.startswith(ns + "/")]
                used = [f"{k} job {j['id']}" for k, j in jobs if j.get("store") == store and (j.get("ns") == ns or str(j.get("ns") or "").startswith(ns + "/"))]
                from .models import BackupPolicy
                pols = [f"policy {p.name}" for p in db.query(BackupPolicy).all() for ly in (p.layers or []) if ly.get("target_id") == str(target.id) and ly.get("store") == store and (ly.get("namespace") == ns or str(ly.get("namespace") or "").startswith(ns + "/"))]
                if ns not in have:
                    reasons.append(f"there is no namespace {ns} on {store}")
                    rules.append("SAFE-PBSJOB-001")
                if groups:
                    reasons.append(f"the namespace still holds backups of {len(groups)} guest{'s' if len(groups) != 1 else ''}: PyXie only deletes an empty namespace")
                    rules.append("SAFE-PBSJOB-001")
                if kids:
                    reasons.append(f"it still has child namespaces ({', '.join(kids)})")
                    rules.append("SAFE-PBSJOB-001")
                if used or pols:
                    reasons.append("it is still used by " + ", ".join(pols + used) + ": change those first")
                    rules.append("SAFE-PBSJOB-001")
                if not rules:
                    reasons.append(f"Delete the empty namespace {ns} from datastore {store}. It holds no backups.")
    if rules:
        return blocked()
    ctx = {"protection_target_id": str(target.id), "target": target.name, "node": target.name, "store": store, "action": action, "clean": clean, "before": before}
    result = {"target": target.name, "node": target.name, "store": store, "action": action, "eligible": True, "reasons": reasons, "blocking_safety_rules": []}
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, context=ctx, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def execute_datastore_change(db: Session, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise OperationError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    ctx = op.context or {}
    target = db.query(ProtectionTarget).filter(ProtectionTarget.id == ctx["protection_target_id"]).one()
    store, action, clean, before = ctx["store"], ctx["action"], ctx.get("clean") or {}, ctx.get("before")
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            try:
                with _read(db, target) as c:
                    cfg = {d["name"]: d for d in (c._get("/config/datastore") or [])}
            except Exception:  # noqa: BLE001
                return block_operation(db, op, blocking_safety_rules=["SAFE-PBSJOB-002"])
            if store not in cfg or (action == "gc_schedule" and cfg[store].get("gc-schedule") != before):
                return block_operation(db, op, blocking_safety_rules=["SAFE-PLAN-CHANGED-001"])
            try:
                acquire_lock(db, resource_type="pbs_target", resource_id=target.id, operation_id=op.id, reason="pbs.datastore_change", ttl_seconds=900)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"])
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing" and not op.pve_task_result:
            try:
                with PbsWriteClient(load_pbs_credentials(db, target, "administrative")) as w:
                    if action == "gc_schedule":
                        v = clean.get("gc-schedule")
                        w.set_datastore(store, {"gc-schedule": v} if v else {}, [] if v else ["gc-schedule"])
                        result = {"set": True}
                    elif action == "ns_create":
                        w._send("POST", f"/admin/datastore/{store}/namespace", {"name": clean["ns"]})
                        result = {"set": True}
                    elif action == "ns_delete":
                        w._send("DELETE", f"/admin/datastore/{store}/namespace?ns={clean['ns']}")
                        result = {"set": True}
                    elif action == "gc_run":
                        result = {"upid": w.gc_run(store)}
                    elif action == "verify_run":
                        result = {"upid": w.verify_run(store, clean)}
                    else:
                        w.set_protected(store, clean)
                        result = {"set": True}
            except MutationsDisabledError as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=str(exc))
            except (PbsApiError, Exception) as exc:  # noqa: BLE001
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"PBS refused the change: {exc}")
            op.pve_task_result = result
            db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            release_locks_for_operation(db, op.id)
            problems: list[str] = []
            with _read(db, target) as c:
                if action == "gc_schedule":
                    got = {d["name"]: d for d in (c._get("/config/datastore") or [])}.get(store, {}).get("gc-schedule")
                    if (got or None) != (clean.get("gc-schedule") or None):
                        problems.append("the schedule does not read back as set")
                elif action in ("ns_create", "ns_delete"):
                    have = [x.get("ns", "") for x in (c._get(f"/admin/datastore/{store}/namespace") or [])]
                    if (clean["ns"] in have) != (action == "ns_create"):
                        problems.append("the namespace list does not read back as changed")
                elif action == "protect":
                    snaps = c.snapshots(store, clean["backup-type"], clean["backup-id"], clean.get("ns", "")) or []
                    s = next((x for x in snaps if x.get("backup-time") == clean["backup-time"]), None)
                    if s is None:
                        problems.append("the backup was not found after the change")
                    elif bool(s.get("protected")) != bool(clean["protected"]):
                        problems.append("the protected flag does not read back as set")
                elif not (op.pve_task_result or {}).get("upid"):
                    problems.append("PBS did not return a task id")
            op.verification_result = {"store": store, "action": action, "task": (op.pve_task_result or {}).get("upid"), "problems": problems}
            db.commit()
            write_audit_event(db, event_category="protection", event_type=f"pbs.datastore.{action}", actor=op.approved_by or "system", actor_type="user",
                              metadata={"target": target.name, "store": store, "clean": clean})
            if problems:
                return fail_operation(db, op, error="; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
