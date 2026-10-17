"""Reading what backup servers look like right now, for the policy planner: datastores (garbage collection), namespaces, sync and prune jobs, the remote that points at the server itself,
and which Proxmox storages are backup server datastores. Read-only."""
from __future__ import annotations

from .credentials import load_pve_credentials
from .models import ProtectionTarget, PveTarget
from .pbs_client import PbsClient
from .pbs_server import PbsNotConfigured, load_pbs_credentials
from .pve_write_client import PveMaintenanceClient

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def pick_local_remote(remotes: list[dict], hostname: str) -> str | None:
    """The remote a copy job pulls through to reach its own server. A loopback address wins over the server's own address, then the name, so the answer never depends
    on the order PBS lists them in (a server often has both a 'localhost' remote and an older one that uses its LAN address). Pure."""
    loop = sorted(r["name"] for r in remotes if str(r.get("host")) in LOCAL_HOSTS)
    own = sorted(r["name"] for r in remotes if str(r.get("host")) == hostname)
    return (loop or own or [None])[0]


def read_pbs_state(db) -> dict[str, dict]:
    """{backup server id: {"name","hostname","stores","namespaces","sync","prune","remote"}}. Servers with no write token, or that cannot be reached, are left out
    (the planner then says why a copy to them cannot be planned)."""
    out: dict[str, dict] = {}
    for t in db.query(ProtectionTarget).all():
        try:
            creds = load_pbs_credentials(db, t, "administrative")
        except PbsNotConfigured:
            continue
        try:
            with PbsClient(creds) as c:
                cfg = {d["name"]: d for d in (c._get("/config/datastore") or [])}
                stores = {}
                for d in c.datastores() or []:
                    n = d["store"]
                    conf = cfg.get(n, {})
                    s3 = (d.get("backend-type") == "s3") or ("s3" in str(conf.get("backend", "")))
                    stores[n] = {"gc_schedule": conf.get("gc-schedule"), "backend": "s3" if s3 else "filesystem"}
                ns = {}
                for n in stores:
                    try:
                        ns[n] = [x.get("ns", "") for x in (c._get(f"/admin/datastore/{n}/namespace") or [])]
                    except Exception:  # noqa: BLE001
                        ns[n] = [""]
                remotes = c.remotes() or []
                local = pick_local_remote(remotes, str(t.hostname))
                out[str(t.id)] = {"name": t.name, "hostname": t.hostname, "stores": stores, "namespaces": ns, "sync": c.job_config("sync") or [], "prune": c.job_config("prune") or [], "verify": c.job_config("verify") or [], "remote": local}
        except Exception:  # noqa: BLE001
            continue
    return out


def source_map(db, pve_target: PveTarget) -> dict[str, dict]:
    """{Proxmox storage: {"target_id","datastore","namespace"}} for the storages that are datastores of a backup server PyXie knows (matched on the server's address)."""
    targets = {str(t.hostname): str(t.id) for t in db.query(ProtectionTarget).all()}
    out: dict[str, dict] = {}
    try:
        with PveMaintenanceClient(load_pve_credentials(db, pve_target, "maintenance")) as adm:
            for s in adm._get("/storage") or []:
                if s.get("type") == "pbs" and str(s.get("server")) in targets and s.get("datastore") and not str(s.get("username") or "").startswith("pyxie-pbs-restore@"):
                    out[s["storage"]] = {"target_id": targets[str(s["server"])], "datastore": s["datastore"], "namespace": s.get("namespace") or ""}
    except Exception:  # noqa: BLE001
        return out
    return out
