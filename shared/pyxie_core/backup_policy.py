"""Backup policies: named bundles (target datastore, schedule, mode, retention) that PyXie turns into Proxmox backup (vzdump) jobs, and who gets which one.

A policy is assigned to the whole cluster (the default), to every VM carrying a tag, or to one VM; a VM can also be marked "no backup". The most specific wins:
VM, then tag (higher priority, then policy name), then cluster. PyXie keeps one Proxmox job per policy (id "pyxie-<name>") whose VM list follows the assignments, and
shows any difference as drift to be synced through the normal preview and approval. Jobs PyXie did not create are left alone unless you ask for a VM to be taken out of them.

Everything here is pure (no database, no network) so it is unit tested.
"""

import re

PREFIX = "pyxie-"
COMMENT_PREFIX = "PyXie policy:"


def is_pyxie_job(job: dict) -> bool:
    """A job PyXie wrote itself (its comment says so), not one a person named pyxie-something. Pure."""
    return str(job.get("id", "")).startswith(PREFIX) and str(job.get("comment") or "").startswith(COMMENT_PREFIX)
MODES = ("snapshot", "suspend", "stop")
KEEP = ("keep_last", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}$")
SCHED_RE = re.compile(r"^[A-Za-z0-9*:,./\- ]{3,64}$")
TIME_END_RE = re.compile(r"(\d{1,2}:\d{2}|\*:\d{2}|\*/\d+:\d{2})$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_+.\-]{0,39}$")
UUID_RE = re.compile(r"^[0-9a-fA-F\-]{36}$")


def job_id_for(name: str) -> str:
    """The Proxmox job id for a policy name. Pure."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "policy"
    return (PREFIX + slug)[:48]


def validate_policy(spec: dict) -> tuple[dict, list[str]]:
    """(clean policy, blocking errors). Pure."""
    errors: list[str] = []
    name = str(spec.get("name") or "").strip()
    if not NAME_RE.match(name):
        errors.append("the policy name uses letters, digits, spaces and - _ . (up to 40 characters)")
    storage = str(spec.get("storage") or "").strip()
    if not STORAGE_RE.match(storage):
        errors.append("choose the datastore the backups go to")
    schedule = str(spec.get("schedule") or "").strip()
    if not SCHED_RE.match(schedule) or not TIME_END_RE.search(schedule):
        errors.append("the schedule is a Proxmox calendar event ending in a time, for example 02:00, sat 03:00 or mon..fri 22:30")
    mode = str(spec.get("mode") or "snapshot").strip()
    if mode not in MODES:
        errors.append(f"the backup mode must be one of {', '.join(MODES)}")
    keep: dict[str, int | None] = {}
    for k in KEEP:
        v = spec.get(k)
        if v in (None, "", 0, "0") and k != "keep_last":
            keep[k] = None
            continue
        if v in (None, ""):
            keep[k] = None
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            errors.append(f"{k.replace('_', ' ')} must be a whole number")
            continue
        if not (1 <= n <= 9999):
            errors.append(f"{k.replace('_', ' ')} must be from 1 to 9999 (leave it empty to not use it)")
        keep[k] = n
    notes = str(spec.get("notes_template") or "").strip() or None
    if notes and (len(notes) > 200 or any(c in notes for c in "\n\r")):
        errors.append("the notes template is one line of up to 200 characters")
    from .policy_layers import SCHED_RE as LAYER_SCHED, validate_layers
    layers, lerrs = validate_layers(spec.get("layers"))
    errors += lerrs
    gc = str(spec.get("gc_schedule") or "").strip() or None
    if gc and not LAYER_SCHED.match(gc):
        errors.append("the garbage-collection schedule is not valid (for example daily, or sat 18:15)")
    from .policy_layers import validate_maintenance
    maint, merrs = validate_maintenance(spec.get("maintenance"))
    errors += merrs
    clean = {"name": name, "storage": storage, "schedule": schedule, "mode": mode, "enabled": bool(spec.get("enabled", True)), "notes_template": notes, **keep, "layers": layers, "gc_schedule": gc, "maintenance": maint}
    return clean, errors


def retention_string(policy: dict) -> str | None:
    """'keep-last=3,keep-daily=7' for the keep values that are set; None when nothing is set (keep everything). Pure."""
    parts = [f"{k.replace('_', '-')}={policy[k]}" for k in KEEP if policy.get(k)]
    return ",".join(parts) or None


def parse_retention(value) -> dict[str, int]:
    """'keep-last=3,keep-daily=7' -> {'keep_last': 3, 'keep_daily': 7}. Pure."""
    out: dict[str, int] = {}
    if isinstance(value, dict):  # Proxmox returns the retention of a job as an object when it lists jobs
        return {str(k).replace("-", "_"): int(v) for k, v in value.items() if str(k).startswith("keep-") and str(v).isdigit()}
    for part in str(value or "").split(","):
        k, _, v = part.partition("=")
        if k.startswith("keep-") and v.isdigit():
            out[k.replace("-", "_")] = int(v)
    return out


def describe_retention(policy: dict) -> str:
    rs = retention_string(policy)
    if not rs:
        return "keep every backup (nothing is pruned)"
    names = {"keep_last": "last", "keep_hourly": "hourly", "keep_daily": "daily", "keep_weekly": "weekly", "keep_monthly": "monthly", "keep_yearly": "yearly"}
    return "keep " + ", ".join(f"{policy[k]} {names[k]}" for k in KEEP if policy.get(k))


def job_params(policy: dict, vmids: list[int], *, exclude: list[int] | None = None) -> dict:
    """The Proxmox job definition for a policy and its VM list. With `exclude` (a list, possibly empty) the job is an all-guests job minus those VMs: the shape of the
    default policy, so a VM created later is covered without anyone touching the job. Pure."""
    p = {"id": policy.get("job_id") or job_id_for(policy["name"]), "storage": policy["storage"], "schedule": policy["schedule"], "mode": policy["mode"],
         "enabled": 1 if policy.get("enabled", True) else 0, "comment": f"{COMMENT_PREFIX} {policy['name']}"}
    if exclude is not None:
        p["all"] = 1
        if exclude:
            p["exclude"] = ",".join(str(v) for v in sorted(set(exclude)))
    else:
        p["all"] = 0
        p["vmid"] = ",".join(str(v) for v in sorted(set(vmids)))
    rs = retention_string(policy)
    if rs:
        p["prune-backups"] = rs
    if policy.get("notes_template"):
        p["notes-template"] = policy["notes_template"]
    return p


def _vmids(job: dict) -> list[int]:
    return sorted({int(v) for v in str(job.get("vmid") or "").split(",") if v.strip().isdigit()})


def _excluded(job: dict) -> list[int]:
    return sorted({int(v) for v in str(job.get("exclude") or "").split(",") if v.strip().isdigit()})


def covers(job: dict, vmid: int, all_vmids: set[int]) -> bool:
    """Does an existing Proxmox job back this VM up (explicit list, or all-guests minus excludes)? Pure."""
    if str(job.get("all")) in ("1", "True", "true"):
        excluded = {int(v) for v in str(job.get("exclude") or "").split(",") if v.strip().isdigit()}
        return vmid in all_vmids and vmid not in excluded
    return vmid in _vmids(job)


def resolve_assignments(workloads: list[dict], assignments: list[dict]) -> dict[str, dict]:
    """{workload id: {"policy_id": str | None, "source": "VM" | "tag x" | "cluster"}} for every workload. `workloads`: {id, tags}. `assignments`: {policy_id (None = no backup),
    scope_type cluster|tag|vm, scope_value, priority, policy_name}. Most specific wins; between tags the higher priority, then the policy name. A workload nobody covers is absent. Pure."""
    out: dict[str, dict] = {}
    by_vm = {a["scope_value"]: a for a in assignments if a["scope_type"] == "vm"}
    tag_rules = [a for a in assignments if a["scope_type"] == "tag"]
    cluster_rule = next((a for a in assignments if a["scope_type"] == "cluster"), None)
    for w in workloads:
        wid = str(w["id"])
        if wid in by_vm:
            out[wid] = {"policy_id": by_vm[wid]["policy_id"], "source": "set for this guest"}
            continue
        tags = {str(t).lower() for t in (w.get("tags") or [])}
        mine = sorted((a for a in tag_rules if str(a["scope_value"]).lower() in tags), key=lambda a: (-int(a.get("priority") or 0), str(a.get("policy_name") or "")))
        if mine:
            out[wid] = {"policy_id": mine[0]["policy_id"], "source": f"tag {mine[0]['scope_value']}"}
        elif cluster_rule:
            out[wid] = {"policy_id": cluster_rule["policy_id"], "source": "the default policy"}
    return out


def desired_membership(resolved: dict[str, dict], vmid_of: dict[str, int]) -> dict[str, list[int]]:
    """{policy id: sorted vmids} from the resolved assignments. Pure."""
    out: dict[str, list[int]] = {}
    for wid, r in resolved.items():
        if r["policy_id"] and wid in vmid_of:
            out.setdefault(r["policy_id"], []).append(vmid_of[wid])
    return {k: sorted(set(v)) for k, v in out.items()}


def _same_job(job: dict, want: dict) -> list[str]:
    """What differs between an existing job and the wanted definition (human words). Pure."""
    diffs = []
    job_all = str(job.get("all")) in ("1", "True", "true")
    if str(want.get("all")) in ("1", "True", "true"):
        if not job_all or _excluded(job) != _excluded(want):
            diffs.append("the VM list")
    elif job_all or _vmids(job) != sorted(int(v) for v in str(want.get("vmid") or "").split(",") if v):
        diffs.append("the VM list")
    for k, label in (("storage", "datastore"), ("schedule", "schedule"), ("mode", "mode")):
        if str(job.get(k) or "") != str(want[k]):
            diffs.append(label)
    if bool(int(job.get("enabled", 1))) != bool(want["enabled"]):
        diffs.append("enabled")
    if parse_retention(job.get("prune-backups")) != parse_retention(want.get("prune-backups")):
        diffs.append("retention")
    if (job.get("notes-template") or None) != (want.get("notes-template") or None):
        diffs.append("notes template")
    if (job.get("comment") or None) != (want.get("comment") or None):
        diffs.append("name")
    return diffs


def plan_job_writes(policies: list[dict], membership: dict[str, list[int]], jobs: list[dict], default_id: str | None = None, all_vmids: set[int] | None = None) -> list[dict]:
    """The Proxmox job writes that make the jobs match the policies: [{"action": create|update|delete, "job_id", "params", "why"}]. A policy with no VMs has no job. Jobs whose
    id starts with 'pyxie-' but belong to no policy are deleted too. Pure."""
    existing = {j["id"]: j for j in jobs}
    writes: list[dict] = []
    wanted_ids: set[str] = set()
    for pol in policies:
        vmids = membership.get(pol["id"], [])
        jid = pol.get("job_id") or job_id_for(pol["name"])
        wanted_ids.add(jid)
        if jid in existing and not is_pyxie_job(existing[jid]):
            writes.append({"action": "conflict", "job_id": jid, "params": {}, "why": f"a Proxmox job called {jid} already exists and PyXie did not create it: give the policy a different name"})
            continue
        is_default_pol = default_id is not None and pol["id"] == default_id and all_vmids is not None
        if not vmids and not (is_default_pol and all_vmids):
            if jid in existing:
                writes.append({"action": "delete", "job_id": jid, "params": {}, "why": f"policy {pol['name']} has no VMs left"})
            continue
        is_default = default_id is not None and pol["id"] == default_id and all_vmids is not None
        want = job_params(pol, vmids, exclude=sorted(all_vmids - set(vmids)) if is_default else None)
        if jid not in existing:
            writes.append({"action": "create", "job_id": jid, "params": want, "why": f"policy {pol['name']}: new job for " + ("every guest that has no policy of its own" if is_default else f"{len(vmids)} VM{'s' if len(vmids) != 1 else ''}")})
        else:
            diffs = _same_job(existing[jid], want)
            if diffs:
                writes.append({"action": "update", "job_id": jid, "params": want, "why": f"policy {pol['name']}: {', '.join(diffs)} changed"})
    for jid in existing:
        if is_pyxie_job(existing[jid]) and jid not in wanted_ids:
            writes.append({"action": "delete", "job_id": jid, "params": {}, "why": "no policy uses this PyXie job any more"})
    return writes


def leave_other_jobs(jobs: list[dict], vmids: set[int], all_vmids: set[int]) -> list[dict]:
    """For VMs now covered by a policy: the changes to jobs PyXie did not create so they stop backing those VMs up twice. [{"job_id", "action": exclude|remove, "vmids"}]. Pure."""
    out = []
    for j in jobs:
        if is_pyxie_job(j):
            continue
        hit = sorted(v for v in vmids if covers(j, v, all_vmids))
        if not hit:
            continue
        is_all = str(j.get("all")) in ("1", "True", "true")
        item = {"job_id": j["id"], "action": "exclude" if is_all else "remove", "vmids": hit}
        if not is_all and not [v for v in _vmids(j) if v not in hit]:
            item["leaves_empty"] = True  # a job with no VMs left cannot be written: the person must delete that job themselves
        out.append(item)
    return out
