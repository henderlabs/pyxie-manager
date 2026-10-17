"""Backup policies as operations (backup.policy_change): save or delete a policy, change who it is assigned to, or sync the jobs with the assignments.

Same Safety Contract as every other operation: dry-run -> approve -> revalidate -> lock -> execute -> read back -> audit. The preview works out the exact Proxmox backup-job writes
(create, update or delete one job per policy, plus taking VMs out of jobs PyXie did not create when you ask), shows which VMs join or leave what, and checks the Proxmox privileges
(Datastore.AllocateSpace on the datastore and VM.Backup on the VMs). The PyXie database changes only after the Proxmox writes succeed, and the result is read back from Proxmox.
"""

import uuid

from .pbs_state import read_pbs_state, source_map
from .policy_layers import plan_pbs
from .backup_policy import TAG_RE, covers, describe_retention, desired_membership, job_id_for, leave_other_jobs, plan_job_writes, resolve_assignments, validate_policy
from .credentials import load_pve_credentials
from .discovery import build_pve_client
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .models import BackupAssignment, BackupPolicy, Cluster, Operation, PveTarget, Workload
from .operations_engine import OperationError, approve_operation, block_operation, create_operation, enter_stage, fail_operation
from .pve_permissions import friendly_error, missing_privileges, permission_reason
from .pve_write_client import MutationsDisabledError, PveMaintenanceClient
from . import rollback

OP_TYPE = "backup.policy_change"
ACTIONS = ("save", "delete", "assign", "sync")


class BackupPolicyError(Exception):
    pass


def _target(db, cluster: Cluster) -> PveTarget:
    return db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()


def policy_dict(p: BackupPolicy) -> dict:
    return {"id": str(p.id), "name": p.name, "storage": p.storage, "schedule": p.schedule, "mode": p.mode, "enabled": p.enabled, "notes_template": p.notes_template, "job_id": p.job_id, "layers": list(p.layers or []), "gc_schedule": p.gc_schedule, "maintenance": dict(p.maintenance or {}),
            **{k: getattr(p, k) for k in ("keep_last", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly")}}


def assignment_dict(a: BackupAssignment, names: dict[str, str]) -> dict:
    return {"id": str(a.id), "policy_id": str(a.policy_id) if a.policy_id else None, "scope_type": a.scope_type, "scope_value": a.scope_value, "priority": a.priority,
            "policy_name": names.get(str(a.policy_id), "") if a.policy_id else ""}


def load_state(db, cluster: Cluster) -> tuple[list[dict], list[dict]]:
    pols = [policy_dict(p) for p in db.query(BackupPolicy).filter(BackupPolicy.cluster_id == cluster.id).order_by(BackupPolicy.name).all()]
    names = {p["id"]: p["name"] for p in pols}
    asg = [assignment_dict(a, names) for a in db.query(BackupAssignment).filter(BackupAssignment.cluster_id == cluster.id).all()]
    return pols, asg


def guests(db, cluster: Cluster) -> list[dict]:
    return [{"id": str(w.id), "vmid": w.vmid, "name": w.name or f"VM {w.vmid}", "tags": list(w.tags or []), "type": w.type}
            for w in db.query(Workload).filter(Workload.cluster_id == cluster.id, Workload.is_missing.is_(False), Workload.is_template.isnot(True)).order_by(Workload.vmid).all()]


def apply_change(policies: list[dict], assignments: list[dict], change: dict, vm_ids: set[str] | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """The policies and assignments after a change, and blocking errors. Pure."""
    pols, asg, errors = [dict(p) for p in policies], [dict(a) for a in assignments], []
    action = change["action"]
    if action == "save":
        clean, errs = validate_policy(change["policy"])
        errors += errs
        pid = change["policy"].get("id") or change.get("new_id")
        others = [p for p in pols if p["id"] != pid]
        if any(p["name"].lower() == clean["name"].lower() for p in others):
            errors.append(f"a policy called '{clean['name']}' already exists")
        if any(p["job_id"] == job_id_for(clean["name"]) for p in others if p.get("job_id")):
            errors.append("that name makes the same Proxmox job id as another policy: choose a more different name")
        existing = next((p for p in pols if p["id"] == pid), None)
        if change["policy"].get("id") and existing is None:
            errors.append("that policy no longer exists")
        entry = {**clean, "id": pid, "job_id": (existing or {}).get("job_id") or job_id_for(clean["name"])}
        pols = [entry if p["id"] == pid else p for p in pols] if existing else pols + [entry]
        tags, make_default, terrs = scope_of_save(change["policy"])
        errors += terrs
        if not terrs:
            asg = apply_scope(asg, pid, clean["name"], tags, make_default)
    elif action == "delete":
        pid = change["policy_id"]
        if not any(p["id"] == pid for p in pols):
            errors.append("that policy no longer exists")
        users = [a for a in asg if a["policy_id"] == pid]
        if users:
            what = [("the default for every guest" if a["scope_type"] == "cluster" else f"guests tagged {a['scope_value']}" if a["scope_type"] == "tag" else "a guest set to it by hand") for a in users]
            errors.append("this policy is still in use (" + ", ".join(sorted(set(what))) + "): give those guests another policy first, then delete it")
        pols = [p for p in pols if p["id"] != pid]
    elif action == "assign":
        names = {p["id"]: p["name"] for p in pols}
        for c in change.get("clear", []):
            asg = [a for a in asg if not (a["scope_type"] == c["scope_type"] and (a["scope_value"] or None) == (c.get("scope_value") or None))]
        for s in change.get("set", []):
            if s["scope_type"] not in ("cluster", "tag", "vm"):
                errors.append("an assignment applies to the cluster, a tag or one VM")
                continue
            if s.get("policy_id") and s["policy_id"] not in names:
                errors.append("that policy no longer exists")
                continue
            if s["scope_type"] == "cluster" and not s.get("policy_id"):
                errors.append("the default must be a policy (to back up nothing by default, assign nothing)")
                continue
            if s["scope_type"] == "tag" and not TAG_RE.match(str(s.get("scope_value") or "")):
                errors.append(f"'{s.get('scope_value')}' is not a valid tag")
                continue
            if s["scope_type"] == "vm" and vm_ids is not None and str(s.get("scope_value")) not in vm_ids:
                errors.append("that VM is not part of this cluster")
                continue
            try:
                s["priority"] = int(s.get("priority") or 0)
            except (TypeError, ValueError):
                errors.append("the priority is a whole number")
                continue
            asg = [a for a in asg if not (a["scope_type"] == s["scope_type"] and (a["scope_value"] or None) == (s.get("scope_value") or None))]
            asg.append({"id": None, "policy_id": s.get("policy_id"), "scope_type": s["scope_type"], "scope_value": s.get("scope_value"), "priority": int(s.get("priority") or 0),
                        "policy_name": names.get(s.get("policy_id"), "")})
    return pols, asg, errors


def scope_of_save(policy: dict) -> tuple[list[str] | None, bool, list[str]]:
    """(tags the policy applies to or None when the form did not say, make it the default, errors). Pure."""
    errs: list[str] = []
    tags = policy.get("tags")
    if tags is not None:
        tags = sorted({str(t).strip() for t in tags if str(t).strip()})
        errs += [f"'{t}' is not a valid tag" for t in tags if not TAG_RE.match(t)]
    return tags, bool(policy.get("make_default")), errs


def apply_scope(asg: list[dict], pid: str, name: str, tags: list[str] | None, make_default: bool) -> list[dict]:
    """The assignments after a save: the policy's tag rules follow `tags` (None = leave them), and it becomes the cluster default when asked. Pure."""
    out = [dict(a) for a in asg]
    if tags is not None:
        out = [a for a in out if not (a["scope_type"] == "tag" and a["policy_id"] == pid and a["scope_value"] not in tags)]
        for t in tags:
            mine = next((a for a in out if a["scope_type"] == "tag" and a["scope_value"] == t), None)
            if mine:
                mine["policy_id"], mine["policy_name"] = pid, name
            else:
                out.append({"id": None, "policy_id": pid, "scope_type": "tag", "scope_value": t, "priority": 0, "policy_name": name})
    if make_default:
        out = [a for a in out if a["scope_type"] != "cluster"] + [{"id": None, "policy_id": pid, "scope_type": "cluster", "scope_value": None, "priority": 0, "policy_name": name}]
    return out


def _names(gs: list[dict]) -> dict[int, str]:
    return {g["vmid"]: g["name"] for g in gs}


def build_plan(policies, assignments, gs: list[dict], jobs: list[dict], leave_other: bool, pbs: tuple | None = None) -> dict:
    resolved = resolve_assignments([{"id": g["id"], "tags": g["tags"]} for g in gs], assignments)
    membership = desired_membership(resolved, {g["id"]: g["vmid"] for g in gs})
    default_id = next((a["policy_id"] for a in assignments if a["scope_type"] == "cluster" and a.get("policy_id")), None)
    all_vmids = {g["vmid"] for g in gs}
    writes = plan_job_writes(policies, membership, jobs, default_id, all_vmids)
    others = leave_other_jobs(jobs, {v for vs in membership.values() for v in vs}, all_vmids) if leave_other else []
    plan = {"membership": membership, "writes": writes, "others": others, "resolved": resolved, "pbs_writes": [], "pbs_others": [], "pbs_errors": []}
    if pbs is not None:
        state, source_of = pbs
        pp = plan_pbs(policies, membership, {g["vmid"]: g["type"] for g in gs}, default_id, all_vmids, source_of, state, leave_other)
        plan["pbs_writes"], plan["pbs_others"], plan["pbs_errors"] = pp["writes"], pp["others"], pp["errors"]
    return plan


def dry_run_policy_change(db, cluster: Cluster, *, change: dict, leave_other: bool = True, actor: str, correlation_id=None) -> Operation:
    if change.get("action") not in ACTIONS:
        raise BackupPolicyError(f"action must be one of {', '.join(ACTIONS)}")
    target = _target(db, cluster)
    if change["action"] == "save" and not change["policy"].get("id"):
        change["new_id"] = str(uuid.uuid4())
    if change["action"] == "save":
        for lay in change["policy"].get("layers") or []:
            if not lay.get("id"):
                lay["id"] = str(uuid.uuid4())  # fixed now so the preview, the approval and the saved policy all mean the same layer
    ctx = {"cluster": cluster.name, "change": change, "leave_other": bool(leave_other)}
    op = create_operation(db, OP_TYPE, cluster_id=cluster.id, correlation_id=correlation_id, context=ctx, created_by=actor)
    reasons: list[str] = []
    blocking: list[str] = []
    policies, assignments = load_state(db, cluster)
    gs = guests(db, cluster)
    new_pols, new_asg, errs = apply_change(policies, assignments, change, {g["id"] for g in gs})
    if errs:
        reasons += errs
        blocking.append("SAFE-BACKUPPOL-001")
    plan = {"writes": [], "others": [], "membership": {}, "pbs_writes": [], "pbs_others": [], "pbs_errors": []}
    jobs: list[dict] = []
    if not errs:
        try:
            client, _cred = build_pve_client(db, target)
            with client:
                jobs = client.backup_jobs()
            plan = build_plan(new_pols, new_asg, gs, jobs, leave_other, (read_pbs_state(db), source_map(db, target)))
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"could not read the backup jobs from Proxmox: {str(exc)[:160]}")
            blocking.append("SAFE-BACKUPPOL-002")
    if not blocking:
        names = _names(gs)
        vm_label = lambda v: f"{names.get(v, 'VM')} ({v})"  # noqa: E731
        if change["action"] == "save":
            p = next(p for p in new_pols if p["id"] == (change["policy"].get("id") or change.get("new_id")))
            reasons.append(f"policy '{p['name']}': back up to {p['storage']} at {p['schedule']} ({p['mode']} mode), {describe_retention(p)}" + ("" if p["enabled"] else "; switched off, so its job will not run"))
        elif change["action"] == "delete":
            reasons.append("deletes the policy and its Proxmox job (backups already taken stay on the datastore; VMs it covered are no longer backed up by it)")
        for w in plan["writes"]:
            if w["action"] == "conflict":
                reasons.append(w["why"])
                blocking.append("SAFE-BACKUPPOL-001")
                continue
            pol = next((p for p in new_pols if p.get("job_id") == w["job_id"]), None)
            members = plan["membership"].get(pol["id"], []) if pol else []
            what = {"create": "creates", "update": "updates", "delete": "deletes"}[w["action"]]
            reasons.append(f"{what} the Proxmox job {w['job_id']}: {w['why']}" + (f" ({', '.join(vm_label(v) for v in members)})" if members and w["action"] != "delete" else ""))
        pbs_names = {tid: st["name"] for tid, st in (read_pbs_state(db) if (plan["pbs_writes"] or plan["pbs_others"]) else {}).items()}
        for e in plan["pbs_errors"]:
            reasons.append(e)
            blocking.append("SAFE-BACKUPPOL-001")
        for w in plan["pbs_writes"]:
            what = {"create": "creates", "update": "updates", "delete": "deletes"}[w["action"]]
            reasons.append(f"on {pbs_names.get(w['target_id'], 'the backup server')}: {what} the {w['kind']} {w['id']}: {w['why']}")
        for o in plan["pbs_others"]:
            reasons.append(f"on {pbs_names.get(o['target_id'], 'the backup server')}: the sync job {o['id']} (not managed by policies) is told to skip {len(o['vmids'])} guest{'s' if len(o['vmids']) != 1 else ''} the policies copy themselves, so they are not copied twice")
        for o in plan["others"]:
            if o.get("leaves_empty"):
                reasons.append(f"taking {', '.join(vm_label(v) for v in o['vmids'])} out of the job {o['job_id']} would leave that job with no VMs, which Proxmox cannot keep: delete that job in Proxmox yourself, or untick 'take VMs out of other jobs'")
                blocking.append("SAFE-BACKUPPOL-001")
                continue
            reasons.append(("adds to the exclude list of" if o["action"] == "exclude" else "removes from the VM list of") + f" the job {o['job_id']}: {', '.join(vm_label(v) for v in o['vmids'])} (so they are not backed up twice)")
        if not plan["writes"] and not plan["others"] and not plan["pbs_writes"] and not plan["pbs_others"] and change["action"] in ("assign", "sync"):
            reasons.append("the Proxmox jobs already match the assignments: only PyXie's own record changes" if change["action"] == "assign" else "the Proxmox jobs already match: nothing to sync")
            if change["action"] == "sync":
                blocking.append("SAFE-BACKUPPOL-001")
        # the VMs that would be backed up twice if the other jobs are left alone
        if not leave_other:
            dup = leave_other_jobs(jobs, {v for vs in plan["membership"].values() for v in vs}, {g["vmid"] for g in gs})
            for o in dup:
                reasons.append(f"warning: {', '.join(vm_label(v) for v in o['vmids'])} stay in the job {o['job_id']} too, so they are backed up twice")
        # privileges
        adds = sorted({v for w in plan["writes"] if w["action"] not in ("delete", "conflict") for v in str(w["params"].get("vmid", "")).split(",") if v.isdigit()})
        stores_needed = sorted({w["params"]["storage"] for w in plan["writes"] if w["action"] not in ("delete", "conflict")})
        lack: list[str] = []
        if plan["writes"] or plan["others"]:
            lack += missing_privileges(db, target, 0, ["VM.Backup"], path="/vms") or []
            for s in stores_needed:
                lack += [x for x in (missing_privileges(db, target, 0, ["Datastore.AllocateSpace"], path=f"/storage/{s}") or []) if x not in lack]
            lack += [x for x in (missing_privileges(db, target, 0, ["Sys.Modify"], path="/") or []) if x not in lack]
        if lack:
            reasons.append(permission_reason(sorted(set(lack)), "change backup jobs"))
            blocking.append("SAFE-PERMISSION-001")
        if adds and any(p["enabled"] is False for p in new_pols if p["id"] in plan["membership"]):
            reasons.append("a switched-off policy keeps its job but Proxmox does not run it")
    ctx.update({"writes": plan["writes"], "others": plan["others"], "membership": plan["membership"], "pbs_writes": plan["pbs_writes"], "pbs_others": plan["pbs_others"]})
    op.context = ctx
    db.commit()
    result = {"cluster": cluster.name, "action": change["action"], "writes": [{"action": w["action"], "job_id": w["job_id"], "why": w["why"]} for w in plan["writes"]], "others": plan["others"],
              "pbs_writes": [{"action": w["action"], "kind": w["kind"], "id": w["id"], "why": w["why"]} for w in plan["pbs_writes"]],
              "eligible": not blocking, "reasons": reasons, "blocking_safety_rules": sorted(set(blocking)), "workload_name": None}
    if blocking:
        return enter_stage(db, op, status="blocked", stage="dry_run", dry_run_result=result, blocking_safety_rules=sorted(set(blocking)), actor=actor)
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=result, actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db, op: Operation, *, approved_by: str) -> Operation:
    return approve_operation(db, op, approved_by=approved_by)


def _commit_change(db, cluster: Cluster, change: dict, actor: str) -> None:
    """Record the change in PyXie's own tables (called after Proxmox accepted the job writes)."""
    action = change["action"]
    if action == "save":
        clean, _ = validate_policy(change["policy"])
        pid = change["policy"].get("id") or change["new_id"]
        row = db.query(BackupPolicy).filter(BackupPolicy.id == pid).one_or_none()
        if row is None:
            row = BackupPolicy(id=pid, cluster_id=cluster.id, created_by=actor, job_id=job_id_for(clean["name"]))
            db.add(row)
        for k, v in clean.items():
            setattr(row, k, v)
        tags, make_default, _e = scope_of_save(change["policy"])
        if tags is not None:
            for a in db.query(BackupAssignment).filter(BackupAssignment.cluster_id == cluster.id, BackupAssignment.scope_type == "tag", BackupAssignment.policy_id == pid).all():
                if a.scope_value not in tags:
                    db.delete(a)
            for t in tags:
                a = db.query(BackupAssignment).filter(BackupAssignment.cluster_id == cluster.id, BackupAssignment.scope_type == "tag", BackupAssignment.scope_value == t).one_or_none()
                if a:
                    a.policy_id = pid
                else:
                    db.add(BackupAssignment(cluster_id=cluster.id, policy_id=pid, scope_type="tag", scope_value=t, priority=0, created_by=actor))
        if make_default:
            db.query(BackupAssignment).filter(BackupAssignment.cluster_id == cluster.id, BackupAssignment.scope_type == "cluster").delete()
            db.flush()
            db.add(BackupAssignment(cluster_id=cluster.id, policy_id=pid, scope_type="cluster", scope_value=None, priority=0, created_by=actor))
    elif action == "delete":
        db.query(BackupAssignment).filter(BackupAssignment.policy_id == change["policy_id"]).delete()
        db.query(BackupPolicy).filter(BackupPolicy.id == change["policy_id"]).delete()
    elif action == "assign":
        for c in change.get("clear", []):
            q = db.query(BackupAssignment).filter(BackupAssignment.cluster_id == cluster.id, BackupAssignment.scope_type == c["scope_type"])
            q = q.filter(BackupAssignment.scope_value.is_(None)) if not c.get("scope_value") else q.filter(BackupAssignment.scope_value == c["scope_value"])
            q.delete()
        for s in change.get("set", []):
            q = db.query(BackupAssignment).filter(BackupAssignment.cluster_id == cluster.id, BackupAssignment.scope_type == s["scope_type"])
            q = q.filter(BackupAssignment.scope_value.is_(None)) if not s.get("scope_value") else q.filter(BackupAssignment.scope_value == s["scope_value"])
            q.delete()
            db.add(BackupAssignment(cluster_id=cluster.id, policy_id=s.get("policy_id"), scope_type=s["scope_type"], scope_value=s.get("scope_value"), priority=int(s.get("priority") or 0), created_by=actor))
    db.commit()


def execute_policy_change(db, operation_id) -> Operation:
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise BackupPolicyError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")
    cluster = db.query(Cluster).filter(Cluster.id == op.cluster_id).one()
    target = _target(db, cluster)
    ctx = dict(op.context or {})
    change = ctx["change"]
    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            try:
                acquire_lock(db, resource_type="backup_policy", resource_id=cluster.id, operation_id=op.id, reason=OP_TYPE)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            policies, assignments = load_state(db, cluster)
            gs = guests(db, cluster)
            new_pols, new_asg, errs = apply_change(policies, assignments, change, {g["id"] for g in gs})
            rc, _cred = build_pve_client(db, target)
            with rc:
                jobs = rc.backup_jobs()
            plan = build_plan(new_pols, new_asg, gs, jobs, ctx.get("leave_other", True), (read_pbs_state(db), source_map(db, target))) if not errs else None
            same = (plan is not None and plan["writes"] == ctx["writes"] and plan["others"] == ctx["others"]  # the whole plan, parameters and VM lists included
                    and plan["pbs_writes"] == ctx.get("pbs_writes", []) and plan["pbs_others"] == ctx.get("pbs_others", []))
            if not same:
                import json as _json, logging as _logging
                if plan is not None:
                    for _k in ("writes", "others", "pbs_writes", "pbs_others"):
                        if plan[_k] != ctx.get(_k, []):
                            _logging.getLogger(__name__).warning("policy plan changed since the preview (%s): now=%s preview=%s", _k, _json.dumps(plan[_k], sort_keys=True, default=str)[:900], _json.dumps(ctx.get(_k, []), sort_keys=True, default=str)[:900])
                else:
                    _logging.getLogger(__name__).warning("policy change no longer valid: %s", errs)
                release_locks_for_operation(db, op.id)
                return block_operation(db, op, blocking_safety_rules=["SAFE-BACKUPPOL-001"], actor="system")  # something changed since the preview: preview again
            op = enter_stage(db, op, status="executing", stage="executing")
        if op.status == "executing":
            done: list[str] = []
            with PveMaintenanceClient(load_pve_credentials(db, target, "maintenance")) as wc:
                live = {j["id"]: j for j in wc._get("/cluster/backup") or []}
                try:
                    for w in ctx["writes"]:
                        here = w["job_id"] in live  # a resumed run may find some of these already done
                        if w["action"] == "delete":
                            if here:
                                wc.delete_backup_job(w["job_id"])
                        elif here:
                            wc.replace_backup_job(w["job_id"], w["params"], live[w["job_id"]])
                        else:
                            wc.create_backup_job(w["params"])
                        done.append(f"{w['action']} {w['job_id']}")
                    for o in ctx["others"]:
                        cur = live.get(o["job_id"]) or (wc._get(f"/cluster/backup/{o['job_id']}") or {})
                        if o["action"] == "exclude":
                            ex = sorted({int(v) for v in str(cur.get("exclude") or "").split(",") if v.strip().isdigit()} | set(o["vmids"]))
                            wc.update_backup_job(o["job_id"], all_guests=True, exclude=",".join(str(v) for v in ex))
                        else:
                            keep = [v for v in str(cur.get("vmid") or "").split(",") if v.strip().isdigit() and int(v) not in o["vmids"]]
                            wc.update_backup_job(o["job_id"], all_guests=False, vmid=",".join(keep))
                        done.append(f"{o['action']} in {o['job_id']}")
                except Exception as exc:  # noqa: BLE001
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"Proxmox refused a job change ({friendly_error(exc) if 'Permission check failed' in str(exc) else str(exc)[:200]}). Done before that: {', '.join(done) or 'nothing'}. PyXie's records were not changed; Sync shows what still differs")
            if ctx.get("pbs_writes") or ctx.get("pbs_others"):
                try:
                    done += run_pbs_writes(db, ctx.get("pbs_writes") or [], ctx.get("pbs_others") or [])
                except Exception as exc:  # noqa: BLE001
                    release_locks_for_operation(db, op.id)
                    return fail_operation(db, op, error=f"a backup server refused a change ({str(exc)[:240]}). Done before that: {', '.join(done) or 'nothing'}. PyXie's records were not changed; Sync shows what still differs")
            _commit_change(db, cluster, change, op.created_by or "operation")
            op = enter_stage(db, op, status="verifying", stage="verifying")
        if op.status == "verifying":
            policies, assignments = load_state(db, cluster)
            rc, _cred = build_pve_client(db, target)
            with rc:
                jobs = rc.backup_jobs()
            gs = guests(db, cluster)
            plan = build_plan(policies, assignments, gs, jobs, False, (read_pbs_state(db), source_map(db, target)))
            problems = [f"{w['job_id']} still differs ({w['why']})" for w in plan["writes"]]
            problems += [f"{w['kind']} {w['id']} on the backup server still differs ({w['why']})" for w in plan["pbs_writes"]]
            problems += list(plan["pbs_errors"])
            wanted_others = {(o["job_id"], v) for o in ctx["others"] for v in o["vmids"]}
            all_vmids = {g["vmid"] for g in gs}
            problems += [f"{v} is still in {j}" for j, v in sorted(wanted_others) if any(x["id"] == j and covers(x, v, all_vmids) for x in jobs)]
            op.verification_result = {"jobs": [j["id"] for j in jobs], "problems": problems}
            db.commit()
            release_locks_for_operation(db, op.id)
            if problems:
                return fail_operation(db, op, error="read-back after the change did not match: " + "; ".join(problems))
            return enter_stage(db, op, status="completed", stage="audit", rollback_classification=rollback.MANUAL_REVERSIBLE)
        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")
    except (MutationsDisabledError,) as exc:
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=friendly_error(exc) if "Permission check failed" in str(exc) else f"unexpected error: {exc}")


MANAGED_KEYS = ("ignore-verified", "outdated-after", "ns", "group-filter", "transfer-last", "verified-only", "remove-vanished", "max-depth", "keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")


def run_pbs_writes(db, writes: list[dict], others: list[dict]) -> list[str]:
    """Apply the planned backup-server writes, server by server: namespaces first, then limits on old prune jobs, copy jobs, new prune jobs, garbage collection, the
    sync jobs PyXie does not own, and deleted jobs last. A resumed run finds some already done and carries on. Returns what was done."""
    from .models import ProtectionTarget
    from .pbs_client import PbsWriteClient
    from .pbs_server import load_pbs_credentials
    order = {"namespace": 0, "prune_limit": 1, "sync": 2, "verify": 3, "prune": 3, "gc": 4}
    done: list[str] = []
    for tid in sorted({w["target_id"] for w in writes} | {o["target_id"] for o in others}):
        t = db.query(ProtectionTarget).filter(ProtectionTarget.id == tid).one()
        with PbsWriteClient(load_pbs_credentials(db, t, "administrative")) as c:
            have = {k: {j["id"] for j in (c.job_config(k) or [])} for k in ("sync", "prune", "verify")}
            mine = [w for w in writes if w["target_id"] == tid]
            for w in sorted((w for w in mine if w["action"] != "delete"), key=lambda w: (order.get("prune_limit" if (w["kind"] == "prune" and set(w["params"]) == {"max-depth"}) else w["kind"], 9))):
                if w["kind"] == "namespace":
                    if w["id"] not in {x.get("ns", "") for x in (c._get(f"/admin/datastore/{w['store']}/namespace") or [])}:
                        c._send("POST", f"/admin/datastore/{w['store']}/namespace", w["params"])
                elif w["kind"] == "gc":
                    c.set_datastore(w["store"], w["params"])
                elif w["id"] in have[w["kind"]]:
                    p = {k: v for k, v in w["params"].items() if k != "id"}
                    dele = None
                    if "id" in w["params"]:  # a job PyXie owns: whatever it no longer sets is removed; a limit on someone else's job touches only that setting
                        live = next((j for j in (c.job_config(w["kind"]) or []) if j["id"] == w["id"]), {})
                        dele = [k for k in MANAGED_KEYS if k in live and k not in p] or None
                    elif w.get("clear"):  # a job of yours PyXie now manages (its retention): only the limits it no longer sets are removed
                        dele = list(w["clear"])
                    c.update_job(w["kind"], w["id"], p, dele)
                else:
                    c.create_job(w["kind"], w["params"])
                done.append(f"{w['action']} {w['kind']} {w['id']}")
            for o in [o for o in others if o["target_id"] == tid]:
                cur = next((j for j in (c.job_config("sync") or []) if j["id"] == o["id"]), None)
                if cur is None:
                    continue
                filt = list(cur.get("group-filter") or [])
                for f in o["add_filters"]:
                    if f not in filt:
                        filt.append(f)
                c.update_job("sync", o["id"], {"group-filter": filt})
                done.append(f"skip guests in sync {o['id']}")
            for w in [w for w in mine if w["action"] == "delete"]:
                if w["id"] in have[w["kind"]]:
                    c.delete_job(w["kind"], w["id"])
                    done.append(f"delete {w['kind']} {w['id']}")
    return done
