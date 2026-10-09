"""Setup guide: GET /api/setup/status (fast, database only) and POST /api/setup/check (live checks, admin)."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pyxie_core import edge_probe, host_kit
from pyxie_core.credentials import CredentialNotConfigured, load_pve_credentials, resolve_pve_endpoints
from pyxie_core.models import (
    AppSettings, Cluster, HostMaintenanceCredential, Node, NotificationRule, PveCredential, PveTarget, Site, Workload,
)
from pyxie_core.pve_client import PveClient
from pyxie_core.pve_write_client import PveMaintenanceClient
from pyxie_core.setup_status import build_steps, progress, server_checks

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .host_kit import KIT_DIR

router = APIRouter(prefix="/api/setup", tags=["setup"], dependencies=[Depends(get_current_user)])


def _expected_wrapper() -> str:
    try:
        return host_kit.wrapper_version(KIT_DIR)
    except Exception:  # noqa: BLE001
        return "0"


def _inputs(db: Session) -> dict:
    targets = []
    nodes_out = []
    for t in db.query(PveTarget).order_by(PveTarget.name).all():
        creds = {c.slot_name: c.status for c in db.query(PveCredential).filter(PveCredential.pve_target_id == t.id).all()}
        cluster = db.query(Cluster).filter(Cluster.pve_target_id == t.id).first()
        nodes = db.query(Node).filter(Node.cluster_id == cluster.id, Node.is_missing.is_(False)).order_by(Node.name).all() if cluster else []
        targets.append({"id": str(t.id), "name": t.name, "inventory_status": creds.get("inventory"),
                        "maintenance_status": creds.get("maintenance"), "console_credential": "console" in creds, "nodes": len(nodes)})
        for n in nodes:
            nodes_out.append({"name": n.name, "target_id": str(t.id), "pinned": bool(n.ssh_host_key_base64),
                              "wrapper_version": n.wrapper_version, "failover_enabled": n.failover_enabled is not False})
    s = db.query(AppSettings).filter(AppSettings.id == 1).one_or_none()
    return {
        "sites": db.query(Site).count(),
        "targets": targets,
        "nodes": nodes_out,
        "hostmaint": [{"target_id": str(h.pve_target_id), "has_cred": True} for h in db.query(HostMaintenanceCredential).all()],
        "expected_wrapper": _expected_wrapper(),
        "settings": {
            "pve_mutations_enabled": bool(s and s.pve_mutations_enabled),
            "console_enabled": bool(s and s.console_enabled),
            "smtp_enabled": bool(s and s.smtp_enabled),
            "notification_recipient_set": bool(s and s.notification_recipient),
            "notification_rules": db.query(NotificationRule).count(),
        },
    }


@router.get("/status")
def setup_status(db: Session = Depends(get_db)):
    steps = build_steps(_inputs(db))
    return {"steps": steps, "progress": progress(steps), "tls_mode": edge_probe.tls_mode()}


def _chk(group, label, status, detail="", fix=""):
    return {"group": group, "label": label, "status": status, "detail": detail, "fix": fix}


def _endpoint_reachable(host: str, port: int) -> bool:
    """Does this node's Proxmox web service answer? GET / (the login page) is instant. Do NOT probe /api2/json/version
    without a token: Proxmox deliberately delays that 401 by about 3 seconds, which made healthy nodes look down."""
    try:
        r = httpx.get(f"https://{host}:{port}/", verify=False, timeout=6.0)
        return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


@router.post("/check", dependencies=[Depends(require_admin)])
def run_check(db: Session = Depends(get_db)):
    """Live checks, in plain words. Read-only: only version / permission reads and short SSH status probes."""
    from .inventory import get_node_host_maintenance_status

    out: list[dict] = []
    inp = _inputs(db)
    expected = inp["expected_wrapper"]
    s = inp["settings"]

    if not inp["targets"]:
        out.append(_chk("Cluster", "PVE target", "fail", "No PVE target is connected yet.", "Follow steps 2 and 3 above."))

    for t in db.query(PveTarget).order_by(PveTarget.name).all():
        g = f"Cluster {t.name}"
        for slot in ("inventory", "maintenance"):
            if not db.query(PveCredential).filter(PveCredential.pve_target_id == t.id, PveCredential.slot_name == slot).first():
                if slot == "inventory":
                    out.append(_chk(g, "Read-only (inventory) token", "fail", "Not saved.", "Add it on the target."))
                else:
                    out.append(_chk(g, "Maintenance (Admin) token", "info", "Not saved. Needed for migrations, power actions and reboots.", "Add it on the Credentials page."))
                continue
            label = "Read-only (inventory) token" if slot == "inventory" else "Maintenance (Admin) token"
            try:
                creds = load_pve_credentials(db, t, slot)
                with PveClient(creds) as c:
                    ver = c.version()
                out.append(_chk(g, label, "ok", f"Works. Proxmox VE {ver.get('version', '?') if isinstance(ver, dict) else ver}."))
            except Exception as exc:  # noqa: BLE001
                out.append(_chk(g, label, "fail", f"Does not work: {str(exc)[:140]}", "Check the token secret and the role grants (Test connection)."))

        # Console permission, only when the switch is on
        if s["console_enabled"]:
            try:
                try:
                    creds = load_pve_credentials(db, t, "console")
                    which = "console token"
                except CredentialNotConfigured:
                    creds = load_pve_credentials(db, t, "maintenance")
                    which = "maintenance token"
                with PveMaintenanceClient(creds) as c:
                    perms = c._get("/access/permissions") or {}
                has = any("VM.Console" in (v or {}) for v in perms.values())
                out.append(_chk(g, "Embedded console permission", "ok" if has else "fail",
                                f"The {which} has VM.Console." if has else f"The {which} does not have VM.Console.",
                                "" if has else "Re-run the Proxmox account script with console access, or add VM.Console to the role."))
            except Exception as exc:  # noqa: BLE001
                out.append(_chk(g, "Embedded console permission", "warn", f"Could not check: {str(exc)[:120]}"))

        # Ingress / failover
        hosts, port = resolve_pve_endpoints(db, t)
        hosts = list(dict.fromkeys(hosts))[:8]
        with ThreadPoolExecutor(max_workers=4) as ex:
            up = list(ex.map(lambda h: _endpoint_reachable(h, port), hosts))
        reachable = [h for h, ok in zip(hosts, up) if ok]
        nodes = [n for n in inp["nodes"] if n["target_id"] == str(t.id)]
        disabled = [n["name"] for n in nodes if not n["failover_enabled"]]
        if len(hosts) < 2:
            out.append(_chk(g, "Ingress / failover", "warn",
                            f"PyXie knows only one way into this cluster ({hosts[0]}). If that node is down, PyXie loses Proxmox.",
                            "Make sure discovery has run so the other nodes' management addresses are known."))
        elif len(reachable) < 2:
            out.append(_chk(g, "Ingress / failover", "warn", f"{len(reachable)} of {len(hosts)} entry points answer right now.", "Check the nodes that do not answer."))
        else:
            note = f" Failover is turned off for: {', '.join(disabled)}." if disabled else ""
            out.append(_chk(g, "Ingress / failover", "ok", f"{len(reachable)} of {len(hosts)} nodes can act as the entry point.{note}"))

    # Hosts (SSH wrapper)
    nodes_q = db.query(Node).filter(Node.is_missing.is_(False)).order_by(Node.name).all()

    def probe(n):
        try:
            return n, get_node_host_maintenance_status(n.id, db=db)
        except Exception as exc:  # noqa: BLE001
            return n, {"error": str(exc)[:120]}

    for n, st in [probe(n) for n in nodes_q]:
        g = f"Host {n.name}"
        if not st.get("provisioned"):
            why = st.get("error") or ("SSH host key not pinned" if not st.get("host_key_pinned") else "wrapper not installed or not reachable")
            out.append(_chk(g, "Host wrapper", "warn", f"Not connected: {why}.", "Run the host script from the builder on this node, then pin its SSH host key."))
        elif not st.get("reachable"):
            out.append(_chk(g, "Host wrapper", "fail", f"Not reachable: {st.get('error') or 'no answer'}.", "Check SSH to the node and that the wrapper is installed."))
        elif host_kit.is_outdated(st.get("wrapper_version"), expected):
            out.append(_chk(g, "Host wrapper", "warn", f"Version {st.get('wrapper_version')}; {expected} is available. Updates work; live host output needs the new one.", "Run the host script from the builder on this node."))
        else:
            out.append(_chk(g, "Host wrapper", "ok", f"Version {st.get('wrapper_version')}, reachable, host key pinned."))

    try:
        import shutil

        from sqlalchemy import text as _text

        disk = shutil.disk_usage("/update" if os.path.isdir("/update") else "/")
        meminfo = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo").read().splitlines() if ":" in l and len(l.split()) > 1}
        db_bytes = db.execute(_text("select pg_database_size(current_database())")).scalar() or 0
        out.extend(server_checks(
            cpus=os.cpu_count() or 1, mem_total_mb=meminfo.get("MemTotal", 0) // 1024, mem_avail_mb=meminfo.get("MemAvailable", 0) // 1024,
            disk_total_gb=disk.total / 1e9, disk_free_gb=disk.free / 1e9, db_mb=db_bytes / 1e6, objects=len(nodes_q) + db.query(Workload).filter(Workload.is_missing.is_(False)).count(),
        ))
    except Exception as exc:  # noqa: BLE001
        out.append(_chk("This server", "CPU, memory and disk", "info", f"Could not read: {str(exc)[:100]}"))

    out.append(_chk("Settings", "Writes to Proxmox", "ok" if s["pve_mutations_enabled"] else "info",
                    "On." if s["pve_mutations_enabled"] else "Off: previews work, nothing is changed until you switch it on in Settings."))
    out.append(_chk("Settings", "Embedded console", "ok" if s["console_enabled"] else "info", "On." if s["console_enabled"] else "Off (optional)."))
    out.append(_chk("Notifications", "Email alerts", "ok" if (s["smtp_enabled"] and s["notification_recipient_set"]) else "warn",
                    f"Email relay on, {s['notification_rules']} rule(s)." if (s["smtp_enabled"] and s["notification_recipient_set"]) else "No working email setup, so alerts will not reach anyone.",
                    "" if (s["smtp_enabled"] and s["notification_recipient_set"]) else "Settings > Email."))
    order = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
    summary = {k: sum(1 for c in out if c["status"] == k) for k in order}
    return {"checks": out, "summary": summary, "ran_at": datetime.now(timezone.utc).isoformat()}
