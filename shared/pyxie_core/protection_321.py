"""3-2-1 backup posture: pure scoring plus a read-only PBS reader.

3-2-1 means: 3 copies of the data (the live guest plus two backups), on 2 kinds
of storage, with 1 copy offsite. PyXie reads what Proxmox VE and Proxmox
Backup Server (PBS) already do and scores it; it changes nothing here.

A PBS datastore counts as offsite when it is S3-backed or its name says
"offsite". A copy only counts when it is recent (backup_max_age_h for local
datastores, offsite_max_age_h for offsite ones).
"""

from datetime import datetime, timezone
from typing import Optional

BACKUP_MAX_AGE_H = 48
OFFSITE_MAX_AGE_H = 24 * 8


def is_offsite(store: dict) -> bool:
    return store.get("backend") == "s3" or "offsite" in str(store.get("name", "")).lower()


def schedule_allowance_h(schedules: list) -> Optional[int]:
    """How old a copy may be before it is late, given how often the job that feeds it runs (PBS calendar text).
    monthly -> 35 days, weekly -> 9 days, anything else (daily or a time of day) -> 48 hours. None if no schedule."""
    best = None
    for sc in schedules:
        t = str(sc or "").strip().lower()
        if not t:
            continue
        h = 24 * 35 if t in ("monthly",) or t.startswith("*-*-01") or t.startswith("*-01") else (24 * 9 if t in ("weekly",) or t[:3] in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") else 48)
        best = max(best or 0, h)
    return best


def _age_h(ts: Optional[int], now: float) -> Optional[float]:
    return None if not ts else max(0.0, (now - ts) / 3600)


def fmt_age(hours: Optional[float]) -> str:
    if hours is None:
        return "never"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def score_guest(last_by_store: dict, stores: list[dict], covered_by: list[str], now: float,
                backup_max_age_h: int = BACKUP_MAX_AGE_H, offsite_max_age_h: int = OFFSITE_MAX_AGE_H) -> dict:
    """last_by_store: {store_name: newest backup unix time or None}. Returns the per-guest scorecard row."""
    copies = []
    for st in stores:
        ts = last_by_store.get(st["name"])
        age = _age_h(ts, now)
        limit = (st.get("max_age_h") or offsite_max_age_h) if is_offsite(st) else backup_max_age_h
        copies.append({
            "store": st["name"], "offsite": is_offsite(st), "backend": st["backend"],
            "last": ts, "age_h": age, "fresh": age is not None and age <= limit,
            "age_text": fmt_age(age),
        })
    fresh = [c for c in copies if c["fresh"]]
    n_copies = 1 + len(fresh)
    media = {"live"} | {c["backend"] for c in fresh}
    has_offsite = any(c["offsite"] for c in fresh)
    problems = []
    if not covered_by:
        problems.append("No backup job includes it")
    # say exactly which place is late, how late, and what is allowed, instead of a count
    for c, st in zip(copies, stores):
        if c["fresh"]:
            continue
        limit = (st.get("max_age_h") or offsite_max_age_h) if is_offsite(st) else backup_max_age_h
        allowed = f"{int(limit // 24)} days" if limit >= 48 and limit % 24 == 0 else f"{int(limit)} hours"
        where = f"{c['store']}{' (offsite)' if c['offsite'] else ''}"
        problems.append(f"{where}: no backup yet" if not c["last"] else f"{where}: newest backup is {c['age_text'].replace(' ago', '')} old, should be under {allowed}")
    if fresh and len(media) < 2:
        problems.append("All backups are on the same kind of storage")
    if fresh and not has_offsite and not any(c["offsite"] for c in copies):
        problems.append("No offsite datastore has this guest")
    ok = n_copies >= 3 and len(media) >= 2 and has_offsite
    status = "ok" if ok else ("none" if not fresh else "partial")
    return {"copies": copies, "copy_count": n_copies, "media_count": len(media), "offsite": has_offsite,
            "status": status, "problems": problems, "covered_by": covered_by}


def store_notes(store: dict, prune: list[dict], verify: list[dict], sync_into: list[dict], newest: Optional[int], now: float,
                offsite_max_age_h: int = OFFSITE_MAX_AGE_H) -> list[dict]:
    """Warnings about one datastore, as {level, text}."""
    notes = []
    name = store["name"]
    if not prune:
        notes.append({"level": "warn", "text": "No prune job: old backups are never removed here"})
    for p in prune:
        if p.get("last-run-state") not in (None, "OK"):
            notes.append({"level": "bad", "text": f"Prune job {p['id']} last run: {p.get('last-run-state')}"})
    if not verify:
        notes.append({"level": "warn", "text": "No verify job: backups are never checked for corruption"})
    if store.get("gc_known") and not store.get("gc_schedule"):
        notes.append({"level": "warn", "text": "No garbage collection schedule: space from pruned backups is only freed when someone starts garbage collection by hand"
                      + (" (on S3 storage the unused data keeps costing money)" if store.get("backend") == "s3" else "")})
    for v in verify:
        if v.get("last-run-state") not in (None, "OK"):
            notes.append({"level": "bad", "text": f"Verify job {v['id']} last run: {v.get('last-run-state')}"})
    if is_offsite(store):
        limit = store.get("max_age_h") or offsite_max_age_h
        age = _age_h(newest, now)
        if age is None:
            notes.append({"level": "bad", "text": "Offsite store is empty"})
        elif age > limit:
            notes.append({"level": "bad", "text": f"Newest offsite backup is {fmt_age(age)}, later than its schedule allows ({int(limit // 24)} days)"})
        if not sync_into:
            notes.append({"level": "info", "text": "No sync job is visible to PyXie. If one feeds this store, its API token may lack permission to list it"})
    return notes


def summarize(rows: list[dict]) -> dict:
    out = {"ok": 0, "partial": 0, "none": 0}
    for r in rows:
        out[r["status"]] += 1
    out["total"] = len(rows)
    return out


def describe_retention(job: dict) -> str:
    parts = [f"{k.replace('keep-', '')} {job[k]}" for k in ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly") if job.get(k)]
    return "keep " + ", ".join(parts) if parts else "keep all"


def read_pbs(client) -> dict:
    """Read-only. Everything the scorecard needs from one PBS: datastores, newest backup per guest per datastore,
    and the prune, verify and sync jobs with their last run. Missing endpoints (permissions) degrade to empty."""
    cfg = {d["name"]: d for d in (client._get("/config/datastore") or [])}
    stores, last = [], {}
    for d in client.datastores() or []:
        name = d["store"]
        c = cfg.get(name, {})
        backend = d.get("backend-type") or ("s3" if "s3" in str(c.get("backend", "")) else "filesystem")
        newest, groups_n, snaps = None, 0, 0
        groups = []
        for ns in (client.namespaces(name) if hasattr(client, "namespaces") else [""]):  # a copy in a namespace is still a copy on this datastore
            try:
                groups += (client._get(f"/admin/datastore/{name}/groups", {"ns": ns}) if ns else client.groups(name)) or []
            except Exception:
                continue
        for g in groups:
            groups_n += 1
            snaps += int(g.get("backup-count") or 0)
            ts = g.get("last-backup")
            if g.get("backup-type") in ("vm", "ct") and ts:
                key = (g["backup-type"], str(g["backup-id"]))
                last.setdefault(key, {})[name] = max(ts, last.get(key, {}).get(name, 0))
                newest = max(newest or 0, ts)
        stores.append({"name": name, "backend": backend, "gc_schedule": c.get("gc-schedule"), "gc_known": bool(c), "path": c.get("path"), "groups": groups_n, "snapshots": snaps, "newest": newest})

    def jobs(path):
        try:
            return client._get(path) or []
        except Exception:
            return []
    return {"stores": stores, "last": last, "prune": jobs("/admin/prune"), "verify": jobs("/admin/verify"), "sync": jobs("/admin/sync")}
