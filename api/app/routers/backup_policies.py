"""Backup policies API: the policies, who they apply to, the Proxmox jobs behind them, drift, the previews, and one VM's backup picture for its Backup tab."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.backup_policy import covers, describe_retention, desired_membership, is_pyxie_job, plan_job_writes, resolve_assignments
from pyxie_core.backup_policy_workflow import BackupPolicyError, dry_run_policy_change, guests, load_state
from pyxie_core.credentials import load_pve_credentials
from pyxie_core.models import Cluster, Node, PveTarget, Workload
from pyxie_core.retention_workflow import advisories
from pyxie_core.pbs_state import read_pbs_state, source_map
from pyxie_core.policy_layers import describe_keep, three_two_one
from pyxie_core.protection_321 import is_offsite
from pyxie_core.pve_write_client import PveMaintenanceClient

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .operations import _serialize

router = APIRouter(prefix="/api/backup-policies", tags=["backup-policies"], dependencies=[Depends(get_current_user)])


def _cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.query(Cluster).filter(Cluster.id == cluster_id).one_or_none()
    if c is None:
        raise HTTPException(404, "cluster not found")
    return c


def _pve(db: Session, cluster: Cluster):
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return PveMaintenanceClient(load_pve_credentials(db, target, "maintenance"))


def _in_job_words(j: dict) -> str:
    return "every guest" if str(j.get("all")) in ("1", "True", "true") else "listed guests"


def _pbs_view(db: Session) -> dict:
    """{datastore name: {'store': {...gc_schedule...}, 'prune': [jobs]}} read from every saved backup server. Missing or unreachable servers just add nothing."""
    from pyxie_core.pbs_client import PbsClient
    from pyxie_core.protection_321 import read_pbs
    from pyxie_core.protection_scorecard import _pbs_targets
    out: dict = {}
    for _t, creds in _pbs_targets(db):
        try:
            with PbsClient(creds) as c:
                r = read_pbs(c)
        except Exception:  # noqa: BLE001
            continue
        for s in r["stores"]:
            out[s["name"]] = {"store": s, "prune": [j for j in r["prune"] if j.get("store") == s["name"]]}
    return out


@router.get("")
def overview(cluster_id: uuid.UUID, db: Session = Depends(get_db)):
    cluster = _cluster(db, cluster_id)
    policies, assignments = load_state(db, cluster)
    gs = guests(db, cluster)
    try:
        with _pve(db, cluster) as wc:
            jobs = wc._get("/cluster/backup") or []
            raw_stores = [s for s in wc._get("/storage") or [] if "backup" in str(s.get("content") or "") and not s.get("disable")
                          and not str(s.get("username") or "").startswith("pyxie-pbs-restore@")]  # the read-only restore storage can never take a backup
            stores = [{"name": s["storage"], "type": s.get("type"), "datastore": s.get("datastore"), "namespace": s.get("namespace") or ""} for s in raw_stores]
            ds_of = {s["storage"]: s.get("datastore") for s in raw_stores if s.get("type") == "pbs"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"could not read the backup jobs from Proxmox: {str(exc)[:200]}")
    resolved = resolve_assignments([{"id": g["id"], "tags": g["tags"]} for g in gs], assignments)
    membership = desired_membership(resolved, {g["id"]: g["vmid"] for g in gs})
    all_vmids = {g["vmid"] for g in gs}
    default_id = next((a["policy_id"] for a in assignments if a["scope_type"] == "cluster" and a.get("policy_id")), None)
    drift = plan_job_writes(policies, membership, jobs, default_id, all_vmids)
    pol_by_id = {p["id"]: p for p in policies}
    job_by_id = {j["id"]: j for j in jobs}
    pbs_info = _pbs_view(db)
    pstate = read_pbs_state(db)
    sources = source_map(db, db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one())
    servers = [{"id": tid, "name": st["name"], "has_remote": bool(st["remote"]),
                "stores": [{"name": n, "backend": v["backend"], "gc_schedule": v["gc_schedule"], "namespaces": [x for x in st["namespaces"].get(n, []) if x],
                            "prune": [{"id": j["id"], "ns": j.get("ns") or "", "summary": describe_keep({k.replace("-", "_"): j[k] for k in j if k.startswith("keep-")})} for j in st["prune"] if j.get("store") == n]} for n, v in st["stores"].items()]}
               for tid, st in pstate.items()]
    out_pol = []
    for p in policies:
        j = job_by_id.get(p["job_id"])
        dsn = ds_of.get(p["storage"])
        info = pbs_info.get(dsn) if dsn else None
        keep = {k.replace("_", "-"): p[k] for k in ("keep_last", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly") if p.get(k)}
        users = [a for a in assignments if a["policy_id"] == p["id"]]
        src = sources.get(p["storage"])
        sst = (pstate.get(src["target_id"]) or {}).get("stores", {}).get(src["datastore"]) if src else None
        copies = [{"label": "Backup", "backend": (sst or {}).get("backend", "filesystem"), "offsite": bool(sst and is_offsite({"name": src["datastore"], "backend": sst["backend"]}))}]
        view_layers = []
        for ly in p.get("layers") or []:
            ls = (pstate.get(ly["target_id"]) or {}).get("stores", {}).get(ly["store"])
            copies.append({"label": ly["label"], "backend": (ls or {}).get("backend", "filesystem"), "offsite": bool(ls and is_offsite({"name": ly["store"], "backend": ls["backend"]}))})
            view_layers.append({**ly, "server": (pstate.get(ly["target_id"]) or {}).get("name"), "text": f"{ly['label']}: copy to {ly['store']}{'/' + ly['namespace'] if ly['namespace'] else ''} ({ly['schedule']}), " + (describe_keep(ly) if ly["namespace"] else "kept by the datastore's own prune jobs")})
        out_pol.append({**p, "layers": view_layers, "three_two_one": three_two_one(copies), "is_default": p["id"] == default_id, "tags": sorted(a["scope_value"] for a in users if a["scope_type"] == "tag"),
                        "guest_count": sum(1 for r in resolved.values() if r["policy_id"] == p["id"]), "set_by_hand": sum(1 for a in users if a["scope_type"] == "vm"), "datastore": dsn, "warnings": advisories(keep, dsn, info["store"] if info else None, info["prune"] if info else []), "retention": describe_retention(p), "members": membership.get(p["id"], []), "job": ({"id": j["id"], "enabled": bool(int(j.get("enabled", 1))), "next_run": j.get("next-run")} if j else None)})
    out_guests = []
    for g in gs:
        r = resolved.get(g["id"])
        others = [j["id"] for j in jobs if not is_pyxie_job(j) and covers(j, g["vmid"], all_vmids)]
        out_guests.append({**g, "policy_id": r["policy_id"] if r else None, "policy_name": (pol_by_id.get(r["policy_id"]) or {}).get("name") if r else None, "source": r["source"] if r else None,
                           "other_jobs": others})
    other_jobs = [{"id": j["id"], "storage": j.get("storage"), "schedule": j.get("schedule"), "enabled": bool(int(j.get("enabled", 1))), "covers": _in_job_words(j)} for j in jobs if not is_pyxie_job(j)]
    tags = sorted({t for g in gs for t in g["tags"]})
    return {"servers": servers, "policies": out_pol, "assignments": assignments, "guests": out_guests, "other_jobs": other_jobs, "storages": stores, "tags": tags,
            "drift": [{"action": w["action"], "job_id": w["job_id"], "why": w["why"]} for w in drift]}


class ChangeIn(BaseModel):
    cluster_id: uuid.UUID
    change: dict
    leave_other: bool = True


@router.post("/dry-run", dependencies=[Depends(require_admin)])
def dry_run(p: ChangeIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return _serialize(dry_run_policy_change(db, _cluster(db, p.cluster_id), change=p.change, leave_other=p.leave_other, actor=user.email))
    except (BackupPolicyError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc) or "bad request")


@router.get("/workload/{workload_id}")
def workload_backup(workload_id: uuid.UUID, db: Session = Depends(get_db)):
    w = db.query(Workload).filter(Workload.id == workload_id).one_or_none()
    if w is None:
        raise HTTPException(404, "workload not found")
    cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one()
    node = db.query(Node).filter(Node.id == w.node_id).one()
    policies, assignments = load_state(db, cluster)
    gs = guests(db, cluster)
    resolved = resolve_assignments([{"id": g["id"], "tags": g["tags"]} for g in gs], assignments)
    r = resolved.get(str(w.id))
    pol = next((p for p in policies if r and p["id"] == r["policy_id"]), None)
    out = {"policy": ({"id": pol["id"], "name": pol["name"], "retention": describe_retention(pol), "schedule": pol["schedule"], "storage": pol["storage"], "mode": pol["mode"], "source": r["source"], "is_default": any(x["scope_type"] == "cluster" and x["policy_id"] == pol["id"] for x in assignments),
                       "layers": [{"label": "Copy", "text": f"to {ly['store']}{'/' + ly['namespace'] if ly['namespace'] else ''} ({ly['schedule']}); " + (describe_keep(ly) if ly["namespace"] else "kept by the datastore's own prune jobs")} for ly in pol.get("layers") or []]} if pol else None),
           "exempt": bool(r and r["policy_id"] is None), "source": r["source"] if r else None, "jobs": [], "backups": [], "error": None, "policies": [{"id": p["id"], "name": p["name"]} for p in policies],
           "cluster_id": str(cluster.id)}
    try:
        with _pve(db, cluster) as wc:
            jobs = wc._get("/cluster/backup") or []
            all_vmids = {g["vmid"] for g in gs}
            cov = [j for j in jobs if covers(j, w.vmid, all_vmids)]
            out["jobs"] = [{"id": j["id"], "storage": j.get("storage"), "schedule": j.get("schedule"), "enabled": bool(int(j.get("enabled", 1))), "mode": j.get("mode"), "retention": j.get("prune-backups"),
                            "next_run": j.get("next-run"), "by_policy": is_pyxie_job(j), "covers": _in_job_words(j)} for j in cov]
            stores = sorted({j.get("storage") for j in cov if j.get("storage")} | ({pol["storage"]} if pol else set()))
            for s in stores:
                try:
                    items = wc._get(f"/nodes/{node.name}/storage/{s}/content", params={"content": "backup", "vmid": w.vmid}) or []
                except Exception:  # noqa: BLE001
                    items = []
                for i in items:
                    out["backups"].append({"volid": i.get("volid"), "storage": s, "ctime": i.get("ctime"), "size": i.get("size"), "notes": i.get("notes"),
                                           "verified": (i.get("verification") or {}).get("state") if isinstance(i.get("verification"), dict) else None, "protected": bool(i.get("protected"))})
            out["backups"].sort(key=lambda b: b.get("ctime") or 0, reverse=True)
            out["backups"] = out["backups"][:50]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"could not read the backup jobs from Proxmox: {str(exc)[:160]}"
    return out
