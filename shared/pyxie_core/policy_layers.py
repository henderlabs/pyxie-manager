"""Policy layers: the extra copies a backup policy makes after the first backup, and the PBS objects that carry them.

A policy is layer 1 (the Proxmox backup job: where, when, how long) plus any number of copy layers. A copy layer is a PBS sync job ("aux copy") that pulls the
policy's guests from the datastore layer 1 writes to into a target datastore, optionally into its own namespace, with its own retention (a prune job scoped to that
namespace) and, for the datastore as a whole, a garbage-collection schedule. Everything here is pure (no database, no network), so it is unit tested.

PBS facts this relies on (PBS 4.x): namespaces are folders inside a datastore that share its chunk store; sync jobs take `ns`, `max-depth`, `group-filter`
(`group:vm/100`, or `exclude:group:vm/100`), `transfer-last`, `verified-only`, `remove-vanished`; prune jobs take `ns` and `max-depth`; garbage collection is
one schedule per datastore and covers every namespace in it. A prune job left at the datastore root also prunes the child namespaces unless `max-depth` is 0.
"""
from __future__ import annotations

import re
import uuid

PREFIX = "px-"
MARK = "PyXie policy:"
KEEP = ("keep_last", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly")
SCHED_RE = re.compile(r"^[A-Za-z0-9 ,:*/.\-]{1,64}$")
NS_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._\-]{0,31}$")
STORE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")
UUID_RE = re.compile(r"^[0-9a-fA-F\-]{36}$")


def valid_namespace(ns: str) -> bool:
    """'' is the datastore root; otherwise up to 7 levels of simple names joined by '/'. Pure."""
    if ns == "":
        return True
    parts = ns.split("/")
    return len(parts) <= 7 and all(NS_PART.match(p) for p in parts)


def slug(name: str, n: int = 14) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "policy")[:n].strip("-") or "policy"


def job_ids(policy_name: str, layer_id: str) -> tuple[str, str]:
    """(sync job id, prune job id) for a layer. Short and stable: PBS job ids are at most 32 characters. Pure."""
    base = f"{PREFIX}{slug(policy_name)}-{str(layer_id).replace('-', '')[:6]}"
    return base + "-s", base + "-p"


def validate_layer(spec: dict) -> tuple[dict, list[str]]:
    """(clean layer, errors). Pure."""
    errors: list[str] = []
    lid = str(spec.get("id") or "").strip() or str(uuid.uuid4())
    if not UUID_RE.match(lid):
        errors.append("a copy layer id is not valid")
    label = str(spec.get("label") or "").strip() or "Copy"
    if not LABEL_RE.match(label):
        errors.append("a copy's name uses letters, digits, spaces and - _ . (up to 40 characters)")
    target = str(spec.get("target_id") or "").strip()
    if not UUID_RE.match(target):
        errors.append(f"copy '{label}': choose the backup server it goes to")
    store = str(spec.get("store") or "").strip()
    if not STORE_RE.match(store):
        errors.append(f"copy '{label}': choose the datastore it goes to")
    ns = str(spec.get("namespace") or "").strip().strip("/")
    if not valid_namespace(ns):
        errors.append(f"copy '{label}': the namespace uses letters, digits and . _ - (up to 7 levels separated by /)")
    schedule = str(spec.get("schedule") or "").strip()
    if not SCHED_RE.match(schedule):
        errors.append(f"copy '{label}': give a schedule such as daily, monthly or sat 03:00")
    mode = str(spec.get("what") or "all")
    if mode not in ("all", "last"):
        errors.append(f"copy '{label}': what to copy is 'every new backup' or 'the last N per guest'")
    last = None
    if mode == "last":
        try:
            last = int(spec.get("last"))
            if not 1 <= last <= 9999:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"copy '{label}': the number of backups to copy is a whole number from 1 to 9999")
    keep: dict[str, int | None] = {}
    for k in KEEP:
        v = spec.get(k)
        if v in (None, "", 0, "0"):
            keep[k] = None
            continue
        try:
            n = int(v)
            if not 1 <= n <= 9999:
                raise ValueError
            keep[k] = n
        except (TypeError, ValueError):
            errors.append(f"copy '{label}': {k.replace('_', ' ')} is a whole number from 1 to 9999")
            keep[k] = None
    prune_schedule = str(spec.get("prune_schedule") or "daily").strip()
    if not SCHED_RE.match(prune_schedule):
        errors.append(f"copy '{label}': the pruning schedule is not valid")
    gc = str(spec.get("gc_schedule") or "").strip() or None
    if gc and not SCHED_RE.match(gc):
        errors.append(f"copy '{label}': the garbage-collection schedule is not valid")
    clean = {"id": lid, "label": label, "target_id": target, "store": store, "namespace": ns, "schedule": schedule, "what": mode, "last": last,
             "verified_only": bool(spec.get("verified_only")), "remove_vanished": bool(spec.get("remove_vanished")), "prune_schedule": prune_schedule,
             "gc_schedule": gc, "enabled": bool(spec.get("enabled", True)), **keep}
    return clean, errors


def validate_layers(layers) -> tuple[list[dict], list[str]]:
    out, errs, seen = [], [], set()
    for spec in layers or []:
        c, e = validate_layer(spec)
        errs += e
        key = (c["target_id"], c["store"], c["namespace"])
        if key in seen:
            errs.append(f"two copies go to the same place ({c['store']}{'/' + c['namespace'] if c['namespace'] else ''}): use one copy, or a different namespace")
        seen.add(key)
        out.append(c)
    return out, errs


def validate_maintenance(spec) -> tuple[dict, list[str]]:
    """The three switches of a plan: pruning, garbage collection, verification, each a schedule or off (None). Pure."""
    spec = spec or {}
    errs: list[str] = []
    out: dict = {}
    for key, label in (("prune", "pruning"), ("gc", "garbage collection"), ("verify", "verification")):
        v = str(spec.get(key) or "").strip() or None
        if v and not SCHED_RE.match(v):
            errs.append(f"the {label} schedule is not valid (for example daily, or sat 18:15)")
        out[key] = v
    try:
        raw_va = spec.get("verify_after")
        va = 30 if raw_va in (None, "") else int(raw_va)
        if not 1 <= va <= 3650:
            raise ValueError
    except (TypeError, ValueError):
        errs.append("verify again after a number of days from 1 to 3650")
        va = 30
    out["verify_after"] = va
    return out, errs


def keep_params(layer: dict) -> dict:
    return {k.replace("_", "-"): layer[k] for k in KEEP if layer.get(k)}


def describe_keep(layer: dict) -> str:
    names = {"keep_last": "last", "keep_hourly": "hourly", "keep_daily": "daily", "keep_weekly": "weekly", "keep_monthly": "monthly", "keep_yearly": "yearly"}
    parts = [f"{layer[k]} {names[k]}" for k in KEEP if layer.get(k)]
    return "keep " + ", ".join(parts) if parts else "keep everything"


def group_of(vmid: int, kind: str) -> str:
    return f"{'ct' if kind == 'lxc' else 'vm'}/{vmid}"


def sync_filter(is_default: bool, members: list[int], kinds: dict[int, str], all_vmids: set[int]) -> list[str]:
    """The group-filter of a layer's sync job. The default policy copies everyone who has no policy of its own (all minus the others: new guests join by themselves);
    every other policy copies exactly its members. Pure."""
    if is_default:
        return [f"exclude:group:{group_of(v, kinds.get(v, 'qemu'))}" for v in sorted(all_vmids - set(members))]
    return [f"group:{group_of(v, kinds.get(v, 'qemu'))}" for v in sorted(members)]


def is_pyxie_job(job: dict) -> bool:
    return str(job.get("id", "")).startswith(PREFIX) and str(job.get("comment") or "").startswith(MARK)


def plan_pbs(policies: list[dict], membership: dict[str, list[int]], kinds: dict[int, str], default_id: str | None, all_vmids: set[int],
             source_of: dict[str, dict], state: dict[str, dict], leave_other: bool = True) -> dict:
    """What to write on the backup servers so they match the policies' layers.

    `policies`: each with id, name, storage (the Proxmox storage layer 1 writes to), gc_schedule and layers. `source_of`: {Proxmox storage: {"target_id", "datastore"}}.
    `state`: per backup server id {"stores": {name: {"gc_schedule"}}, "namespaces": {store: [ns, ...]}, "sync": [jobs], "prune": [jobs], "remote": local remote name or None}.
    Returns {"writes": [{"target_id","kind","action","id","store","params","why"}], "others": [...], "errors": [...]}. Pure."""
    writes: list[dict] = []
    others: list[dict] = []
    errors: list[str] = []
    wanted: dict[str, set[str]] = {}  # target id -> PyXie job ids that should exist
    gc_want: dict[tuple[str, str], tuple[str | None, str]] = {}
    want_ns: dict[tuple[str, str], set[str]] = {}
    ns_pruned: dict[tuple[str, str], set[str]] = {}

    def want_gc(tid: str, store: str, value: str | None, who: str):
        if not value:
            return
        prev = gc_want.get((tid, store))
        if prev and prev[0] != value:
            errors.append(f"{prev[1]} and {who} ask for different garbage-collection schedules on {store} (one schedule covers the whole datastore): make them the same")
            return
        gc_want[(tid, store)] = (value, who)

    for pol in policies:
        src = source_of.get(pol.get("storage") or "")
        members = membership.get(pol["id"], [])
        is_default = default_id is not None and pol["id"] == default_id
        if src:
            want_gc(src["target_id"], src["datastore"], pol.get("gc_schedule"), f"policy {pol['name']}")
        for layer in pol.get("layers") or []:
            sync_id, prune_id = job_ids(pol["name"], layer["id"])
            tid, store, ns = layer["target_id"], layer["store"], layer["namespace"]
            st = state.get(tid)
            who = f"policy {pol['name']}, copy '{layer['label']}'"
            if st is None:
                errors.append(f"{who}: that backup server cannot be read (is its write token saved under Credentials?)")
                continue
            if store not in st["stores"]:
                errors.append(f"{who}: the datastore {store} does not exist on that backup server")
                continue
            if not src:
                errors.append(f"{who}: the Proxmox storage {pol.get('storage')} is not a backup server datastore, so there is nothing to copy from")
                continue
            if src["target_id"] != tid:
                errors.append(f"{who}: the copy has to be on the same backup server as the first backup for now (copying between servers needs a remote)")
                continue
            if src["datastore"] == store and ns == "":
                errors.append(f"{who}: a copy cannot go to the same place as the first backup")
                continue
            if not st.get("remote"):
                errors.append(f"{who}: the backup server has no remote that points at itself, which a copy needs. In PBS, add a remote named localhost for 127.0.0.1 (Configuration > Remotes)")
                continue
            wanted.setdefault(tid, set())
            want_gc(tid, store, layer.get("gc_schedule"), who)
            have_ns = set(st["namespaces"].get(store, []))
            if ns and ns not in have_ns and (tid, store, ns) not in {(w["target_id"], w["store"], w["id"]) for w in writes if w["kind"] == "namespace"}:
                writes.append({"target_id": tid, "kind": "namespace", "action": "create", "id": ns, "store": store, "params": {"name": ns}, "why": f"{who}: new namespace {ns} on {store}"})
            want_ns.setdefault((tid, store), set()).add(ns)
            if not members and not is_default:
                continue  # nobody uses the policy yet: no job (existing ones are removed below)
            flt = sync_filter(is_default, members, kinds, all_vmids)
            p = {"id": sync_id, "store": store, "remote": st["remote"], "remote-store": src["datastore"], "max-depth": 0, "schedule": layer["schedule"],
                 "comment": f"{MARK} {pol['name']} / {layer['label']}"}
            if ns:
                p["ns"] = ns
            if src.get("namespace"):
                p["remote-ns"] = src["namespace"]
            if flt:
                p["group-filter"] = flt
            if layer["what"] == "last":
                p["transfer-last"] = layer["last"]
            if layer["verified_only"]:
                p["verified-only"] = True
            if layer["remove_vanished"]:
                p["remove-vanished"] = True
            wanted[tid].add(sync_id)
            cur = next((j for j in st["sync"] if j["id"] == sync_id), None)
            if cur is None:
                writes.append({"target_id": tid, "kind": "sync", "action": "create", "id": sync_id, "store": store, "params": p,
                               "why": f"{who}: copy {'every guest without a policy of its own' if is_default else str(len(members)) + ' guest' + ('s' if len(members) != 1 else '')} from {src['datastore']} to {store}{'/' + ns if ns else ''} ({layer['schedule']})"})
            elif not _same_job(cur, p):
                writes.append({"target_id": tid, "kind": "sync", "action": "update", "id": sync_id, "store": store, "params": p, "why": f"{who}: the copy job changed"})
            kp = keep_params(layer)
            if ns and kp:
                pp = {"id": prune_id, "store": store, "ns": ns, "max-depth": 0, "schedule": ((pol.get("maintenance") or {}).get("prune") or layer["prune_schedule"]), **kp, "comment": f"{MARK} {pol['name']} / {layer['label']}"}
                wanted[tid].add(prune_id)
                ns_pruned.setdefault((tid, store), set()).add(ns)
                curp = next((j for j in st["prune"] if j["id"] == prune_id), None)
                if curp is None:
                    writes.append({"target_id": tid, "kind": "prune", "action": "create", "id": prune_id, "store": store, "params": pp,
                                   "why": f"{who}: prune {store}/{ns} ({describe_keep(layer)}, {layer['prune_schedule']})"})
                elif not _same_job(curp, pp):
                    writes.append({"target_id": tid, "kind": "prune", "action": "update", "id": prune_id, "store": store, "params": pp, "why": f"{who}: the pruning changed"})
    # maintenance switches: verification, pruning of the datastore root for the default plan, garbage collection
    verify_done: dict[tuple, str] = {}
    for pol in policies:
        m = pol.get("maintenance") or {}
        if not (m.get("verify") or m.get("prune") or m.get("gc")):
            continue
        src = source_of.get(pol.get("storage") or "")
        members = membership.get(pol["id"], [])
        is_default = default_id is not None and pol["id"] == default_id
        if not members and not is_default:
            continue
        locs = []
        if src:
            locs.append((src["target_id"], src["datastore"], src.get("namespace") or "", {k.replace("_", "-"): pol[k] for k in KEEP if pol.get(k)}, "main"))
        for ly in pol.get("layers") or []:
            locs.append((ly["target_id"], ly["store"], ly["namespace"], keep_params(ly), ly["id"].replace("-", "")[:6]))
        for tid, store, ns, keeps, tag in locs:
            st = state.get(tid)
            if st is None or store not in st["stores"]:
                continue
            who = f"policy {pol['name']}"
            if m.get("gc"):
                want_gc(tid, store, m["gc"], who)
            if m.get("verify"):
                key = (tid, store, ns)
                have = [j for j in st.get("verify", []) if j.get("store") == store and (j.get("ns") or "") == ns and not is_pyxie_job(j)]
                if key not in verify_done and not have:
                    vid = f"px-v-{slug(store, 10)}-{slug(ns, 8) if ns else 'root'}"[:32]
                    vp = {"id": vid, "store": store, "schedule": m["verify"], "ignore-verified": True, "outdated-after": int(m.get("verify_after") or 30),
                          "comment": f"{MARK} {pol['name']} (verification)"}
                    if ns:
                        vp["ns"], vp["max-depth"] = ns, 0
                    verify_done[key] = vid
                    wanted.setdefault(tid, set()).add(vid)
                    cur = next((j for j in st.get("verify", []) if j["id"] == vid), None)
                    if cur is None:
                        writes.append({"target_id": tid, "kind": "verify", "action": "create", "id": vid, "store": store, "params": vp,
                                       "why": f"{who}: verify {store}{'/' + ns if ns else ''} ({m['verify']}, backups checked again after {vp['outdated-after']} days)"})
                    elif not _same_job(cur, vp, ("store", "ns", "max-depth", "schedule", "ignore-verified", "outdated-after")):
                        writes.append({"target_id": tid, "kind": "verify", "action": "update", "id": vid, "store": store, "params": vp, "why": f"{who}: the verification changed"})
            if ns == "" and is_default and keeps and m.get("prune"):
                roots = [j for j in st["prune"] if j.get("store") == store and not j.get("ns") and not is_pyxie_job(j)]
                if len(roots) == 1:
                    j = roots[0]
                    live = {k: j[k] for k in (x.replace("_", "-") for x in KEEP) if j.get(k)}
                    if live != keeps:
                        writes.append({"target_id": tid, "kind": "prune", "action": "update", "id": j["id"], "store": store, "params": dict(keeps), "clear": [k for k in live if k not in keeps],
                                       "why": f"{who}: {store} is pruned by {j['id']}: " + (", ".join(f"{k[5:]} {v}" for k, v in live.items()) or "keeps everything") + " -> " + ", ".join(f"{k[5:]} {v}" for k, v in keeps.items())})
                elif not roots:
                    pid = f"{PREFIX}{slug(pol['name'])}-{tag}-p"[:32]
                    pp = {"id": pid, "store": store, "schedule": m["prune"], **keeps, "comment": f"{MARK} {pol['name']} (pruning)"}
                    wanted.setdefault(tid, set()).add(pid)
                    if not any(j["id"] == pid for j in st["prune"]):
                        writes.append({"target_id": tid, "kind": "prune", "action": "create", "id": pid, "store": store, "params": pp, "why": f"{who}: prune {store} ({', '.join(f'{k[5:]} {v}' for k, v in keeps.items())}, {m['prune']})"})
    # garbage collection schedules
    for (tid, store), (val, who) in sorted(gc_want.items()):
        cur = (state.get(tid) or {"stores": {}})["stores"].get(store, {}).get("gc_schedule")
        if (cur or None) != val:
            writes.append({"target_id": tid, "kind": "gc", "action": "update", "id": store, "store": store, "params": {"gc-schedule": val},
                           "why": f"{who}: garbage collection of {store}: {cur or '(no schedule)'} -> {val} (shared by everything on that datastore)"})
    # prune jobs left at a datastore root must stop at the root once a namespace there has its own pruning
    for (tid, store), nss in sorted(ns_pruned.items()):
        for j in (state.get(tid) or {"prune": []})["prune"]:
            if is_pyxie_job(j) or j.get("store") != store or j.get("ns") or j.get("max-depth") == 0:
                continue
            writes.append({"target_id": tid, "kind": "prune", "action": "update", "id": j["id"], "store": store, "params": {"max-depth": 0},
                           "why": f"the prune job {j['id']} on {store} would also prune the namespace{'s' if len(nss) > 1 else ''} {', '.join(sorted(nss))}: limit it to the datastore root (max-depth 0)"})
    # PyXie-owned jobs no layer wants any more
    for tid, st in state.items():
        for kind in ("sync", "prune", "verify"):
            for j in st.get(kind, []):
                if is_pyxie_job(j) and j["id"] not in wanted.get(tid, set()):
                    writes.append({"target_id": tid, "kind": kind, "action": "delete", "id": j["id"], "store": j.get("store"), "params": {}, "why": f"no policy copy uses the {kind} job {j['id']} any more"})
    # other (unmanaged) sync jobs that would copy the same guests again
    if leave_other:
        copied: dict[tuple[str, str], set[int]] = {}
        for pol in policies:
            src = source_of.get(pol.get("storage") or "")
            members = set(membership.get(pol["id"], []))
            is_default = default_id is not None and pol["id"] == default_id
            if is_default:
                members = set(all_vmids) if not members else members
            for layer in pol.get("layers") or []:
                if src and src["target_id"] == layer["target_id"]:
                    copied.setdefault((layer["target_id"], layer["store"]), set()).update(members)
        for (tid, store), vmids in copied.items():
            for j in (state.get(tid) or {"sync": []})["sync"]:
                if is_pyxie_job(j) or j.get("store") != store or j.get("ns"):
                    continue
                filt = list(j.get("group-filter") or [])
                if any(not f.startswith("exclude:") for f in filt):
                    continue  # an include list: leave it, the person decides
                have = {f for f in filt}
                add = [f"exclude:group:{group_of(v, kinds.get(v, 'qemu'))}" for v in sorted(vmids)]
                add = [a for a in add if a not in have]
                if add:
                    others.append({"target_id": tid, "id": j["id"], "store": store, "add_filters": add, "vmids": sorted(vmids)})
    return {"writes": writes, "others": others, "errors": errors}


_CMP = ("ignore-verified", "outdated-after", "store", "remote", "remote-store", "remote-ns", "ns", "max-depth", "schedule", "transfer-last", "verified-only", "remove-vanished", "keep-last", "keep-hourly", "keep-daily",
        "keep-weekly", "keep-monthly", "keep-yearly")


def _n(k: str, v):
    """A PBS setting as comparable: an unset, false or (for everything but max-depth) zero value are all 'not set'. Pure."""
    if v is None or v is False:
        return None
    if v is True:
        return 1
    if v == 0 and k != "max-depth":
        return None
    return v


def _same_job(live: dict, want: dict, keys: tuple | None = None) -> bool:
    """Does a live PBS job match the wanted parameters (only the fields PyXie sets; the group filter as a set)? Pure."""
    for k in (keys or _CMP):
        if _n(k, live.get(k)) != _n(k, want.get(k)):
            return False
    if sorted(live.get("group-filter") or []) != sorted(want.get("group-filter") or []):
        return False
    return str(live.get("comment") or "") == str(want.get("comment") or "")


def three_two_one(copies: list[dict]) -> dict:
    """3-2-1 for one policy, counted like the Protection scorecard: the live guest is copy 1, then each backup copy; "live" is one kind of storage and each
    backup datastore adds its kind (disk or S3); one copy must be offsite (S3 or named offsite). `copies`: [{"label","backend","offsite"}], first backup first. Pure."""
    n = 1 + len(copies)
    kinds = {"live"} | {c["backend"] for c in copies}
    off = any(c["offsite"] for c in copies)
    missing = []
    if n < 3:
        missing.append(f"{3 - n} more cop{'y' if 3 - n == 1 else 'ies'}")
    if len(kinds) < 2:
        missing.append("a second kind of storage")
    if not off:
        missing.append("an offsite copy")
    return {"ok": not missing, "copies": n, "missing": missing}
