"""Collects the per-workload detail the Reporting page needs -- config,
disks, NICs, snapshots, guest IPs -- into the database.

PVE exposes all of this only through one API call per VM, which is too
slow to do inside discovery (and far too slow to do live on every page
view), so it runs as its own job on a slower cadence and on demand from
the Reporting page's Refresh button. Read-only: PveClient has no write
methods.

Same rule as discovery for failures: a FAILED FETCH is not evidence of
REMOVAL. If a workload's config couldn't be read this pass, its previously
collected rows are left exactly as they were and it is counted as failed,
never silently emptied.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .discovery import build_pve_client
from .models import (
    Cluster,
    Node,
    Provider,
    PveTarget,
    ReportCollectionRun,
    Workload,
    WorkloadConfig,
    WorkloadDisk,
    WorkloadNic,
    WorkloadSnapshot,
)

FETCH_WORKERS = 6

_SIZE_UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
_QEMU_DISK_RE = re.compile(r"^(scsi|virtio|ide|sata)\d+$")
_QEMU_SPECIAL_DISK_RE = re.compile(r"^(efidisk|tpmstate)\d+$")
_LXC_DISK_RE = re.compile(r"^(rootfs|mp\d+)$")
_NET_RE = re.compile(r"^net\d+$")


def now():
    return datetime.now(timezone.utc)


# -- parsing ---------------------------------------------------------------


def _kv(value: str) -> tuple[str, dict]:
    """'local-lvm:vm-1-disk-0,size=32G,cache=none' -> (first, {k: v})."""
    parts = str(value).split(",")
    first = parts[0].strip()
    opts = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            opts[k.strip()] = v.strip()
        elif p.strip():
            opts[p.strip()] = "1"
    return first, opts


def parse_size(text: str | None) -> int | None:
    if not text:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT])?B?", str(text).strip(), re.I)
    if not m:
        return None
    unit = (m.group(2) or "B").upper()
    mult = 1 if unit == "B" else _SIZE_UNITS[unit]
    return int(float(m.group(1)) * mult)


def _flag(v) -> bool | None:
    if v is None:
        return None
    return str(v).strip().lower() in ("1", "yes", "true", "on")


def parse_disks(config: dict, wtype: str) -> list[dict]:
    disks = []
    for key, value in config.items():
        if wtype == "vm":
            if not (_QEMU_DISK_RE.match(key) or _QEMU_SPECIAL_DISK_RE.match(key)):
                continue
        elif not _LXC_DISK_RE.match(key):
            continue
        first, opts = _kv(value)
        is_cdrom = opts.get("media") == "cdrom"
        storage, volume = None, first
        if ":" in first and not first.startswith("/"):
            storage = first.split(":", 1)[0]
        disks.append(
            {
                "slot": key,
                "bus": key.rstrip("0123456789"),
                "storage": storage,
                "volume": volume,
                "size_bytes": parse_size(opts.get("size")),
                "is_cdrom": is_cdrom,
                "cache": opts.get("cache"),
                "discard": opts.get("discard"),
                "ssd": _flag(opts.get("ssd")),
                "iothread": _flag(opts.get("iothread")),
                "backup": _flag(opts.get("backup")),
            }
        )
    return sorted(disks, key=lambda d: d["slot"])


def parse_nics(config: dict, wtype: str) -> list[dict]:
    nics = []
    for key, value in config.items():
        if not _NET_RE.match(key):
            continue
        first, opts = _kv(value)
        nic = {"slot": key, "ip_config": None}
        if wtype == "vm":
            # 'virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=10' -- model=mac is the first part
            model, _, mac = first.partition("=")
            nic["model"] = model or None
            nic["mac"] = (mac or opts.get("macaddr") or "").upper() or None
        else:
            # lxc: 'name=eth0,bridge=vmbr0,hwaddr=..,ip=dhcp,tag=..' -- first part is also a k=v
            opts.update({first.split("=", 1)[0]: first.split("=", 1)[1]} if "=" in first else {})
            nic["model"] = opts.get("type") or "veth"
            nic["mac"] = (opts.get("hwaddr") or "").upper() or None
            ip = opts.get("ip")
            ip6 = opts.get("ip6")
            nic["ip_config"] = ", ".join(x for x in (ip, ip6) if x) or None
        nic["bridge"] = opts.get("bridge")
        tag = opts.get("tag")
        nic["vlan_tag"] = int(tag) if tag and tag.isdigit() else None
        nic["firewall"] = _flag(opts.get("firewall"))
        rate = opts.get("rate")
        try:
            nic["rate_mbps"] = float(rate) if rate else None
        except ValueError:
            nic["rate_mbps"] = None
        nic["link_down"] = _flag(opts.get("link_down"))
        nics.append(nic)
    return sorted(nics, key=lambda n: n["slot"])


def parse_config_fields(config: dict, wtype: str) -> dict:
    def _int(k):
        v = config.get(k)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    if wtype == "vm":
        cpu_type = str(config.get("cpu", "")).split(",")[0] or None
        agent = config.get("agent")
        return {
            "sockets": _int("sockets") or 1,
            "cores_per_socket": _int("cores") or 1,
            "cpu_type": cpu_type,
            "memory_mb": _int("memory"),
            "balloon_mb": _int("balloon"),
            "machine": config.get("machine"),
            "bios": config.get("bios") or "seabios",
            "onboot": _flag(config.get("onboot")),
            "protection": _flag(config.get("protection")),
            "agent_enabled": (_flag(str(agent).split(",")[0].replace("enabled=", "")) if agent is not None else False),
            "description": config.get("description"),
            "hostname": None,
        }
    return {
        "sockets": 1,
        "cores_per_socket": _int("cores"),
        "cpu_type": None,
        "memory_mb": _int("memory"),
        "balloon_mb": _int("swap"),  # lxc has swap, not balloon -- closest comparable figure
        "machine": None,
        "bios": None,
        "onboot": _flag(config.get("onboot")),
        "protection": _flag(config.get("protection")),
        "agent_enabled": None,
        "description": config.get("description"),
        "hostname": config.get("hostname"),
    }


def _agent_ips(interfaces: list[dict]) -> tuple[list[str], dict[str, list[str]]]:
    """(all non-loopback addresses, {MAC: [addresses]}) from guest-agent output."""
    all_ips: list[str] = []
    by_mac: dict[str, list[str]] = {}
    for iface in interfaces:
        mac = (iface.get("hardware-address") or iface.get("hwaddr") or "").upper()
        addrs = []
        for a in iface.get("ip-addresses") or []:
            ip = a.get("ip-address")
            if not ip or ip.startswith("127.") or ip == "::1" or ip.lower().startswith("fe80"):
                continue
            addrs.append(ip)
        if isinstance(iface.get("inet"), str):  # lxc /interfaces shape: 'inet': '10.0.0.5/24'
            addrs.append(iface["inet"].split("/")[0])
        if addrs:
            all_ips.extend(addrs)
            if mac:
                by_mac.setdefault(mac, []).extend(addrs)
    v4_first = lambda ips: sorted(dict.fromkeys(ips), key=lambda ip: ":" in ip)  # noqa: E731 -- IPv4 before IPv6, stable, de-duplicated
    return v4_first(all_ips), {m: v4_first(ips) for m, ips in by_mac.items()}


# -- fetching (thread pool: HTTP only, no DB) -------------------------------


def _fetch_one(client, node_name: str, wl: dict) -> dict:
    wtype, vmid = wl["type"], wl["vmid"]
    out = {"id": wl["id"], "ok": False}
    try:
        config = (client.qemu_config(node_name, vmid) if wtype == "vm" else client.lxc_config(node_name, vmid)) or {}
    except Exception as e:  # noqa: BLE001
        out["error"] = f"config: {e}"
        return out
    out["config"] = config
    try:
        snaps = client.qemu_snapshots(node_name, vmid) if wtype == "vm" else client.lxc_snapshots(node_name, vmid)
        out["snapshots"] = [s for s in snaps if s.get("name") != "current"]
    except Exception:  # noqa: BLE001
        out["snapshots"] = None  # unknown -- keep whatever was stored before
    out["ips"], out["ips_by_mac"], out["ips_source"] = [], {}, "unavailable"
    if wl["status"] == "running":
        try:
            if wtype == "vm" and parse_config_fields(config, wtype)["agent_enabled"]:
                out["ips"], out["ips_by_mac"] = _agent_ips(client.qemu_agent_interfaces(node_name, vmid))
                out["ips_source"] = "agent"
            elif wtype == "lxc":
                out["ips"], out["ips_by_mac"] = _agent_ips(client.lxc_interfaces(node_name, vmid))
                out["ips_source"] = "lxc"
        except Exception:  # noqa: BLE001 -- no agent / no permission / timeout: just no IPs
            out["ips_source"] = "unavailable"
    out["ok"] = True
    return out


# -- storing ----------------------------------------------------------------


def _store(db: Session, wl: Workload, r: dict):
    t = now()
    config = r["config"]
    fields = parse_config_fields(config, wl.type)
    if fields["memory_mb"] is None and wl.memory_bytes:
        fields["memory_mb"] = wl.memory_bytes // (1 << 20)
    cfg = db.get(WorkloadConfig, wl.id) or WorkloadConfig(workload_id=wl.id)
    for k, v in fields.items():
        setattr(cfg, k, v)
    nics = parse_nics(config, wl.type)
    if not r["ips"]:
        # fall back to static addresses from an lxc config so the column isn't empty for them
        static = [n["ip_config"] for n in nics if n["ip_config"] and "dhcp" not in n["ip_config"] and "manual" not in n["ip_config"]]
        if static and wl.type == "lxc":
            r["ips"] = [x.split("/")[0] for s in static for x in s.split(", ")]
            r["ips_source"] = "config"
    cfg.guest_ips = r["ips"] or None
    cfg.guest_ips_source = r["ips_source"]
    cfg.collected_at = t
    db.add(cfg)

    db.query(WorkloadDisk).filter(WorkloadDisk.workload_id == wl.id).delete()
    for d in parse_disks(config, wl.type):
        db.add(WorkloadDisk(workload_id=wl.id, collected_at=t, **d))

    db.query(WorkloadNic).filter(WorkloadNic.workload_id == wl.id).delete()
    for n in nics:
        ips = r["ips_by_mac"].get(n["mac"] or "") or None
        db.add(WorkloadNic(workload_id=wl.id, collected_at=t, ips=ips, **n))

    if r["snapshots"] is not None:
        db.query(WorkloadSnapshot).filter(WorkloadSnapshot.workload_id == wl.id).delete()
        for s in r["snapshots"]:
            ts = s.get("snaptime")
            db.add(
                WorkloadSnapshot(
                    workload_id=wl.id,
                    name=s.get("name"),
                    description=(s.get("description") or "").strip() or None,
                    parent=s.get("parent"),
                    snapshot_time=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                    includes_ram=_flag(s.get("vmstate")),
                    collected_at=t,
                )
            )


def collect_reporting_data(db: Session, actor: str = "schedule") -> dict:
    """Runs one collection pass over every enabled PVE target. Returns a
    summary dict; the run itself is also recorded in report_collection_runs
    (what the Reporting page reads for its 'collected at' line)."""
    run = ReportCollectionRun(started_at=now(), status="running", triggered_by=actor)
    db.add(run)
    db.commit()
    total = collected = failed = 0
    per_cluster: dict = {}
    errors: list[str] = []
    try:
        targets = (
            db.query(PveTarget)
            .join(Provider, PveTarget.provider_id == Provider.id)
            .filter(Provider.enabled.is_(True))
            .all()
        )
        for target in targets:
            try:
                client, _cred = build_pve_client(db, target)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{target.name}: {e}")
                continue
            clusters = {c.id: c for c in db.query(Cluster).filter(Cluster.pve_target_id == target.id).all()}
            nodes = {n.id: n for n in db.query(Node).filter(Node.cluster_id.in_(list(clusters))).all()} if clusters else {}
            workloads = (
                db.query(Workload)
                .filter(Workload.cluster_id.in_(list(clusters)), Workload.is_missing.is_(False))
                .all()
                if clusters
                else []
            )
            by_id = {w.id: w for w in workloads}
            jobs = [
                (nodes[w.node_id].name, {"id": w.id, "type": w.type, "vmid": w.vmid, "status": w.status})
                for w in workloads
                if w.node_id in nodes
            ]
            total += len(jobs)
            with client:
                with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                    results = list(pool.map(lambda j: _fetch_one(client, j[0], j[1]), jobs))
            c_ok = c_fail = 0
            for r in results:
                if not r["ok"]:
                    failed += 1
                    c_fail += 1
                    continue
                _store(db, by_id[r["id"]], r)
                collected += 1
                c_ok += 1
            db.commit()
            for cl in clusters.values():
                per_cluster[cl.name] = {"collected": c_ok, "failed": c_fail}
        run.status = "failed" if (errors and collected == 0) else ("partial" if (failed or errors) else "success")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        run.status = "failed"
        run.error = str(e)
        errors.append(str(e))
    run = db.get(ReportCollectionRun, run.id)
    run.ended_at = now()
    run.workloads_total, run.workloads_collected, run.workloads_failed = total, collected, failed
    run.summary = {"clusters": per_cluster, "errors": errors[:10]}
    if errors and not run.error:
        run.error = "; ".join(errors[:3])
    db.commit()
    return {"status": run.status, "total": total, "collected": collected, "failed": failed}
