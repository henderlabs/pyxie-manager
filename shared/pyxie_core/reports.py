"""RVTools-style report definitions and row builders.

One definition per report (columns + a builder) is the single source of
truth for the Reporting page's tables AND the Excel/CSV exports, so a
column can never appear in one and not the other. Builders read only what
is already in the database (discovery + the reporting collector) -- no PVE
calls -- which is what makes every report instant.

Row values are plain scalars (str/int/float/bool/datetime/None); list-ish
fields are pre-joined to text.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from .models import (
    Cluster,
    Finding,
    Node,
    ReportCollectionRun,
    Site,
    Storage,
    Workload,
    WorkloadConfig,
    WorkloadDisk,
    WorkloadNic,
    WorkloadSnapshot,
)

GIB = 1 << 30
MIB = 1 << 20


@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"  # text | int | num | bool | datetime | severity
    hidden: bool = False  # hidden by default in the UI's column picker (always in exports)


@dataclass
class Scope:
    cluster_ids: set = field(default_factory=set)
    node_ids: set = field(default_factory=set)
    workload_ids: set = field(default_factory=set)

    def describe(self, db: Session) -> str:
        parts = []
        if self.cluster_ids:
            names = [c.name for c in db.query(Cluster).filter(Cluster.id.in_(self.cluster_ids)).all()]
            parts.append("clusters: " + ", ".join(sorted(names)))
        if self.node_ids:
            names = [n.name for n in db.query(Node).filter(Node.id.in_(self.node_ids)).all()]
            parts.append("hosts: " + ", ".join(sorted(names)))
        if self.workload_ids:
            parts.append(f"{len(self.workload_ids)} selected VM(s)/container(s)")
        return "; ".join(parts) or "everything"


def parse_uuid_list(text: Optional[str]) -> set:
    out = set()
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            out.add(UUID(part))
    return out


def _ctx(db: Session, scope: Scope) -> dict:
    """Everything the builders share: the scoped hosts/workloads and the
    lookup tables, computed once per request."""
    clusters = {c.id: c for c in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all()}
    sites = {s.id: s.name for s in db.query(Site).all()}
    all_nodes = {n.id: n for n in db.query(Node).filter(Node.is_missing.is_(False)).all()}
    all_workloads = db.query(Workload).filter(Workload.is_missing.is_(False)).all()

    def wl_ok(w: Workload) -> bool:
        if scope.workload_ids and w.id not in scope.workload_ids:
            return False
        if scope.node_ids and w.node_id not in scope.node_ids:
            return False
        if scope.cluster_ids and w.cluster_id not in scope.cluster_ids:
            return False
        return True

    workloads = [w for w in all_workloads if wl_ok(w) and w.node_id in all_nodes]
    if scope.node_ids:
        node_ids = {i for i in scope.node_ids if i in all_nodes}
    elif scope.workload_ids:
        node_ids = {w.node_id for w in workloads}
    elif scope.cluster_ids:
        node_ids = {i for i, n in all_nodes.items() if n.cluster_id in scope.cluster_ids}
    else:
        node_ids = set(all_nodes)
    if scope.cluster_ids:
        node_ids = {i for i in node_ids if all_nodes[i].cluster_id in scope.cluster_ids}
    wl_ids = [w.id for w in workloads]

    def by_wl(model):
        if not wl_ids:
            return {}
        out: dict = {}
        for row in db.query(model).filter(model.workload_id.in_(wl_ids)).all():
            out.setdefault(row.workload_id, []).append(row)
        return out

    configs = {c.workload_id: c for c in db.query(WorkloadConfig).filter(WorkloadConfig.workload_id.in_(wl_ids)).all()} if wl_ids else {}
    return {
        "clusters": clusters,
        "sites": sites,
        "nodes": all_nodes,
        "node_ids": node_ids,
        "workloads": workloads,
        "configs": configs,
        "disks": by_wl(WorkloadDisk),
        "nics": by_wl(WorkloadNic),
        "snaps": by_wl(WorkloadSnapshot),
        "now": datetime.now(timezone.utc),
    }


def _gib(b):
    return round(b / GIB, 2) if b is not None else None


def _join(items) -> Optional[str]:
    items = [str(i) for i in (items or []) if i]
    return ", ".join(items) or None


def _node_name(ctx, w):
    return ctx["nodes"][w.node_id].name


def _cluster_name(ctx, cluster_id):
    c = ctx["clusters"].get(cluster_id)
    return c.name if c else None


def _site_name(ctx, cluster_id):
    c = ctx["clusters"].get(cluster_id)
    return ctx["sites"].get(c.site_id) if c else None


def _wl_ident(ctx, w) -> dict:
    return {
        "vm": w.name or f"vmid {w.vmid}",
        "vmid": w.vmid,
        "type": "VM" if w.type == "vm" else "Container",
        "cluster": _cluster_name(ctx, w.cluster_id),
        "node": _node_name(ctx, w),
    }


# -- pInfo -------------------------------------------------------------------

VINFO = [
    Col("vm", "VM"),
    Col("vmid", "VMID", "int"),
    Col("type", "Type"),
    Col("status", "Power state"),
    Col("cluster", "Cluster"),
    Col("node", "Host"),
    Col("site", "Site", hidden=True),
    Col("os_type", "OS type"),
    Col("vcpu", "vCPUs", "int"),
    Col("sockets", "Sockets", "int", hidden=True),
    Col("cores_per_socket", "Cores / socket", "int", hidden=True),
    Col("cpu_type", "CPU type", hidden=True),
    Col("memory_mb", "Memory (MiB)", "int"),
    Col("disk_count", "Disks", "int"),
    Col("provisioned_gib", "Provisioned (GiB)", "num"),
    Col("nic_count", "NICs", "int"),
    Col("primary_ip", "Primary IP"),
    Col("ips", "All IPs", hidden=True),
    Col("agent", "Guest agent", hidden=True),
    Col("snapshots", "Snapshots", "int"),
    Col("iso", "ISO attached", hidden=True),
    Col("tags", "Tags"),
    Col("ha_state", "HA state", hidden=True),
    Col("machine", "Machine", hidden=True),
    Col("bios", "BIOS", hidden=True),
    Col("onboot", "Start at boot", "bool", hidden=True),
    Col("protection", "Protected", "bool", hidden=True),
    Col("preferred_host", "Preferred host", hidden=True),
    Col("sensitivity", "Sensitivity", hidden=True),
    Col("downtime_tolerance", "Downtime tolerance", hidden=True),
    Col("placement_notes", "PyXie notes", hidden=True),
    Col("description", "Description", hidden=True),
    Col("hostname", "Hostname", hidden=True),
    Col("first_seen", "First seen (UTC)", "datetime", hidden=True),
    Col("last_seen", "Last seen (UTC)", "datetime", hidden=True),
    Col("collected_at", "Detail collected (UTC)", "datetime", hidden=True),
]


def build_vinfo(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    rows = []
    for w in ctx["workloads"]:
        cfg = ctx["configs"].get(w.id)
        disks = [d for d in ctx["disks"].get(w.id, []) if not d.is_cdrom]
        isos = [d.volume for d in ctx["disks"].get(w.id, []) if d.is_cdrom and d.volume and d.volume != "none"]
        ips = (cfg.guest_ips if cfg else None) or []
        vcpu = (cfg.sockets or 1) * (cfg.cores_per_socket or 1) if cfg and cfg.cores_per_socket else w.cpu_cores
        pref = ctx["nodes"].get(w.preferred_node_id)
        rows.append(
            {
                **_wl_ident(ctx, w),
                "status": w.status,
                "site": _site_name(ctx, w.cluster_id),
                "os_type": w.os_type,
                "vcpu": vcpu,
                "sockets": cfg.sockets if cfg else None,
                "cores_per_socket": cfg.cores_per_socket if cfg else None,
                "cpu_type": cfg.cpu_type if cfg else None,
                "memory_mb": cfg.memory_mb if cfg and cfg.memory_mb else (w.memory_bytes // MIB if w.memory_bytes else None),
                "disk_count": len(disks) if cfg else None,
                "provisioned_gib": _gib(sum(d.size_bytes or 0 for d in disks)) if cfg else None,
                "nic_count": len(ctx["nics"].get(w.id, [])) if cfg else None,
                "primary_ip": ips[0] if ips else None,
                "ips": _join(ips),
                "agent": ("Enabled" if cfg.agent_enabled else "Off") if cfg and cfg.agent_enabled is not None else None,
                "snapshots": len(ctx["snaps"].get(w.id, [])) if cfg else None,
                "iso": _join(isos),
                "tags": _join(w.tags if isinstance(w.tags, list) else (w.tags or "").split(";") if w.tags else []),
                "ha_state": w.ha_state,
                "machine": cfg.machine if cfg else None,
                "bios": cfg.bios if cfg else None,
                "onboot": cfg.onboot if cfg else None,
                "protection": cfg.protection if cfg else None,
                "preferred_host": pref.name if pref else None,
                "sensitivity": w.sensitivity,
                "downtime_tolerance": w.downtime_tolerance,
                "placement_notes": w.placement_notes,
                "description": cfg.description if cfg else None,
                "hostname": cfg.hostname if cfg else None,
                "first_seen": w.first_seen,
                "last_seen": w.last_seen,
                "collected_at": cfg.collected_at if cfg else None,
            }
        )
    rows.sort(key=lambda r: (r["cluster"] or "", r["node"] or "", r["vm"].lower()))
    return rows


# -- pHost -------------------------------------------------------------------

VHOST = [
    Col("cluster", "Cluster"),
    Col("site", "Site", hidden=True),
    Col("node", "Host"),
    Col("status", "Status"),
    Col("management_ip", "Management IP"),
    Col("pve_version", "PVE version"),
    Col("kernel", "Kernel", hidden=True),
    Col("cpu_pct", "CPU %", "num"),
    Col("mem_total_mb", "Memory (MiB)", "int"),
    Col("mem_pct", "Memory %", "num"),
    Col("uptime_days", "Uptime (days)", "num"),
    Col("vm_count", "VMs", "int"),
    Col("ct_count", "Containers", "int"),
    Col("running", "Running", "int"),
    Col("alloc_vcpu", "Allocated vCPUs", "int"),
    Col("alloc_mem_mb", "Allocated memory (MiB)", "int"),
    Col("pending_updates", "Pending updates", "int"),
    Col("maintenance", "In maintenance", "bool"),
    Col("maintenance_reason", "Maintenance reason", hidden=True),
    Col("ssh_pinned", "SSH host key pinned", "bool", hidden=True),
    Col("first_seen", "First seen (UTC)", "datetime", hidden=True),
    Col("last_seen", "Last seen (UTC)", "datetime", hidden=True),
]


def build_vhost(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    # host totals describe what is ON the host, not only the selected VMs
    per_node: dict = {}
    for w in db.query(Workload).filter(Workload.is_missing.is_(False)).all():
        s = per_node.setdefault(w.node_id, {"vm": 0, "ct": 0, "running": 0, "vcpu": 0, "mem": 0})
        s["vm" if w.type == "vm" else "ct"] += 1
        s["running"] += 1 if w.status == "running" else 0
        s["vcpu"] += w.cpu_cores or 0
        s["mem"] += (w.memory_bytes or 0) // MIB
    rows = []
    for nid in ctx["node_ids"]:
        n = ctx["nodes"][nid]
        s = per_node.get(nid, {"vm": 0, "ct": 0, "running": 0, "vcpu": 0, "mem": 0})
        rows.append(
            {
                "cluster": _cluster_name(ctx, n.cluster_id),
                "site": _site_name(ctx, n.cluster_id),
                "node": n.name,
                "status": n.status,
                "management_ip": n.management_ip,
                "pve_version": n.pve_version,
                "kernel": n.kernel_version,
                "cpu_pct": n.cpu_usage_pct,
                "mem_total_mb": n.mem_total_bytes // MIB if n.mem_total_bytes else None,
                "mem_pct": n.mem_usage_pct,
                "uptime_days": round(n.uptime_seconds / 86400, 1) if n.uptime_seconds is not None else None,
                "vm_count": s["vm"],
                "ct_count": s["ct"],
                "running": s["running"],
                "alloc_vcpu": s["vcpu"],
                "alloc_mem_mb": s["mem"],
                "pending_updates": n.pending_updates,
                "maintenance": n.maintenance_mode,
                "maintenance_reason": n.maintenance_reason,
                "ssh_pinned": bool(n.ssh_host_key_fingerprint),
                "first_seen": n.first_seen,
                "last_seen": n.last_seen,
            }
        )
    rows.sort(key=lambda r: (r["cluster"] or "", r["node"]))
    return rows


# -- pStorage ----------------------------------------------------------------

VSTORAGE = [
    Col("cluster", "Cluster"),
    Col("site", "Site", hidden=True),
    Col("node", "Host"),
    Col("name", "Storage"),
    Col("type", "Type"),
    Col("scope", "Scope"),
    Col("status", "Status"),
    Col("capacity_gib", "Capacity (GiB)", "num"),
    Col("used_gib", "Used (GiB)", "num"),
    Col("free_gib", "Free (GiB)", "num"),
    Col("used_pct", "Used %", "num"),
    Col("vm_count", "VMs using", "int"),
    Col("vdisk_count", "Virtual disks", "int"),
    Col("provisioned_gib", "Provisioned (GiB)", "num"),
    Col("last_seen", "Last seen (UTC)", "datetime", hidden=True),
]


def build_vstorage(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    eff_clusters = {ctx["nodes"][i].cluster_id for i in ctx["node_ids"]}
    # disks per storage, over ALL workloads (storage capacity is about the
    # storage, not just whichever VMs were selected)
    wl_info = {w.id: (w.node_id, w.cluster_id) for w in db.query(Workload).filter(Workload.is_missing.is_(False)).all()}
    usage: dict = {}
    for d in db.query(WorkloadDisk).filter(WorkloadDisk.is_cdrom.is_(False)).all():
        if d.workload_id not in wl_info or not d.storage:
            continue
        node_id, cluster_id = wl_info[d.workload_id]
        for key in ((node_id, d.storage), (cluster_id, d.storage)):
            u = usage.setdefault(key, {"vms": set(), "disks": 0, "bytes": 0})
            u["vms"].add(d.workload_id)
            u["disks"] += 1
            u["bytes"] += d.size_bytes or 0
    rows = []
    for s in db.query(Storage).filter(Storage.is_missing.is_(False)).all():
        if s.node_id is not None:
            if s.node_id not in ctx["node_ids"]:
                continue
        elif s.cluster_id not in eff_clusters:
            continue
        u = usage.get((s.node_id, s.name) if s.node_id else (s.cluster_id, s.name), {"vms": set(), "disks": 0, "bytes": 0})
        cap, used = s.capacity_bytes, s.used_bytes
        node = ctx["nodes"].get(s.node_id)
        cluster_id = s.cluster_id or (node.cluster_id if node else None)
        rows.append(
            {
                "cluster": _cluster_name(ctx, cluster_id),
                "site": _site_name(ctx, cluster_id),
                "node": node.name if node else None,
                "name": s.name,
                "type": s.type,
                "scope": s.scope,
                "status": s.status,
                "capacity_gib": _gib(cap),
                "used_gib": _gib(used),
                "free_gib": _gib(cap - used) if cap is not None and used is not None else None,
                "used_pct": round(used / cap * 100, 1) if cap and used is not None else None,
                "vm_count": len(u["vms"]),
                "vdisk_count": u["disks"],
                "provisioned_gib": _gib(u["bytes"]),
                "last_seen": s.last_seen,
            }
        )
    rows.sort(key=lambda r: (r["cluster"] or "", r["node"] or "", r["name"]))
    return rows


# -- pDisk -------------------------------------------------------------------

VDISK = [
    Col("vm", "VM"),
    Col("vmid", "VMID", "int"),
    Col("type", "Type"),
    Col("cluster", "Cluster"),
    Col("node", "Host"),
    Col("slot", "Disk"),
    Col("bus", "Bus"),
    Col("storage", "Storage"),
    Col("volume", "Volume"),
    Col("size_gib", "Size (GiB)", "num"),
    Col("cache", "Cache", hidden=True),
    Col("discard", "Discard", hidden=True),
    Col("ssd", "SSD emulation", "bool", hidden=True),
    Col("iothread", "IO thread", "bool", hidden=True),
    Col("backup", "Included in backup", "bool"),
    Col("collected_at", "Collected (UTC)", "datetime", hidden=True),
]


def build_vdisk(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    rows = []
    for w in ctx["workloads"]:
        for d in ctx["disks"].get(w.id, []):
            if d.is_cdrom:
                continue
            rows.append(
                {
                    **_wl_ident(ctx, w),
                    "slot": d.slot,
                    "bus": d.bus,
                    "storage": d.storage,
                    "volume": d.volume,
                    "size_gib": _gib(d.size_bytes),
                    "cache": d.cache,
                    "discard": d.discard,
                    "ssd": d.ssd,
                    "iothread": d.iothread,
                    # PVE default is included; only an explicit backup=0 excludes
                    "backup": True if d.backup is None else d.backup,
                    "collected_at": d.collected_at,
                }
            )
    rows.sort(key=lambda r: (r["cluster"] or "", r["node"] or "", r["vm"].lower(), r["slot"]))
    return rows


# -- pNetwork ----------------------------------------------------------------

VNETWORK = [
    Col("vm", "VM"),
    Col("vmid", "VMID", "int"),
    Col("type", "Type"),
    Col("cluster", "Cluster"),
    Col("node", "Host"),
    Col("slot", "NIC"),
    Col("model", "Adapter"),
    Col("mac", "MAC address"),
    Col("bridge", "Bridge"),
    Col("vlan_tag", "VLAN", "int"),
    Col("ips", "IP addresses"),
    Col("ip_config", "IP config", hidden=True),
    Col("firewall", "Firewall", "bool", hidden=True),
    Col("rate_mbps", "Rate limit (MB/s)", "num", hidden=True),
    Col("link_down", "Link down", "bool", hidden=True),
    Col("collected_at", "Collected (UTC)", "datetime", hidden=True),
]


def build_vnetwork(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    rows = []
    for w in ctx["workloads"]:
        for n in ctx["nics"].get(w.id, []):
            rows.append(
                {
                    **_wl_ident(ctx, w),
                    "slot": n.slot,
                    "model": n.model,
                    "mac": n.mac,
                    "bridge": n.bridge,
                    "vlan_tag": n.vlan_tag,
                    "ips": _join(n.ips),
                    "ip_config": n.ip_config,
                    "firewall": n.firewall,
                    "rate_mbps": n.rate_mbps,
                    "link_down": n.link_down,
                    "collected_at": n.collected_at,
                }
            )
    rows.sort(key=lambda r: (r["cluster"] or "", r["node"] or "", r["vm"].lower(), r["slot"]))
    return rows


# -- pSnapshot ---------------------------------------------------------------

VSNAPSHOT = [
    Col("vm", "VM"),
    Col("vmid", "VMID", "int"),
    Col("type", "Type"),
    Col("cluster", "Cluster"),
    Col("node", "Host"),
    Col("name", "Snapshot"),
    Col("description", "Description"),
    Col("created", "Created (UTC)", "datetime"),
    Col("age_days", "Age (days)", "int"),
    Col("parent", "Parent", hidden=True),
    Col("includes_ram", "Includes RAM", "bool", hidden=True),
    Col("collected_at", "Collected (UTC)", "datetime", hidden=True),
]


def build_vsnapshot(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    rows = []
    for w in ctx["workloads"]:
        for s in ctx["snaps"].get(w.id, []):
            rows.append(
                {
                    **_wl_ident(ctx, w),
                    "name": s.name,
                    "description": s.description,
                    "created": s.snapshot_time,
                    "age_days": (ctx["now"] - s.snapshot_time).days if s.snapshot_time else None,
                    "parent": s.parent,
                    "includes_ram": s.includes_ram,
                    "collected_at": s.collected_at,
                }
            )
    rows.sort(key=lambda r: (r["cluster"] or "", r["node"] or "", r["vm"].lower(), r["created"] or datetime.min.replace(tzinfo=timezone.utc)))
    return rows


# -- pHealth -----------------------------------------------------------------
# RVTools' vHealth equivalent: things that deserve a look. Combines hygiene
# checks computed here from the reporting inventory with PyXie's own active
# findings, so there is one place to look. Findings already cover quorum,
# offline hosts, pending updates, storage thresholds, task failures and
# protection -- those are included as-is, not re-implemented.

SNAPSHOT_WARN_DAYS = 7
SNAPSHOT_CRIT_DAYS = 30
HOST_MEM_ALLOC_WARN_PCT = 90  # same threshold the Hosts & Clusters page warns at
DETAIL_STALE_HOURS = 24
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

VHEALTH = [
    Col("severity", "Severity", "severity"),
    Col("check", "Check"),
    Col("object_type", "Object type"),
    Col("object", "Object"),
    Col("cluster", "Cluster"),
    Col("node", "Host"),
    Col("detail", "Detail"),
    Col("source", "Source", hidden=True),
    Col("since", "Since (UTC)", "datetime", hidden=True),
]


def build_vhealth(db: Session, scope: Scope) -> list[dict]:
    ctx = _ctx(db, scope)
    rows: list[dict] = []
    now = ctx["now"]

    def add(severity, check, object_type, obj, cluster, node, detail, source="Check", since=None):
        rows.append(
            {"severity": severity, "check": check, "object_type": object_type, "object": obj,
             "cluster": cluster, "node": node, "detail": detail, "source": source, "since": since}
        )

    for w in ctx["workloads"]:
        ident = _wl_ident(ctx, w)
        who = dict(object_type=ident["type"], obj=ident["vm"], cluster=ident["cluster"], node=ident["node"])
        cfg = ctx["configs"].get(w.id)
        for s in ctx["snaps"].get(w.id, []):
            if not s.snapshot_time:
                continue
            age = (now - s.snapshot_time).days
            if age >= SNAPSHOT_WARN_DAYS:
                add("critical" if age >= SNAPSHOT_CRIT_DAYS else "warning", "Snapshot age", detail=f"Snapshot '{s.name}' is {age} days old", since=s.snapshot_time, **who)
        for d in ctx["disks"].get(w.id, []):
            if d.is_cdrom:
                if d.volume and d.volume != "none":
                    add("info", "ISO attached", detail=f"{d.slot}: {d.volume}", **who)
            elif d.backup is False:
                size = f"{_gib(d.size_bytes):g} GiB " if d.size_bytes else ""
                add("warning", "Disk excluded from backup", detail=f"{d.slot} ({size}on {d.storage or 'unknown storage'}) has backup disabled", **who)
        if w.status == "running" and w.type == "vm" and cfg is not None:
            if cfg.agent_enabled and cfg.guest_ips_source == "unavailable":
                add("warning", "Guest agent not responding", detail="Agent is enabled but did not answer (agent not running in the guest, or guest not ready)", **who)
            elif cfg.agent_enabled is False:
                add("info", "Guest agent not enabled", detail="No guest agent: IPs unavailable and graceful shutdown relies on ACPI", **who)
        if cfg is not None and (now - cfg.collected_at).total_seconds() > DETAIL_STALE_HOURS * 3600:
            add("warning", "Details out of date", detail=f"Config/disk/NIC details last collected {cfg.collected_at:%Y-%m-%d %H:%M} UTC", since=cfg.collected_at, **who)

    alloc: dict = {}
    for w in db.query(Workload).filter(Workload.is_missing.is_(False)).all():
        alloc[w.node_id] = alloc.get(w.node_id, 0) + (w.memory_bytes or 0)
    for nid in ctx["node_ids"]:
        n = ctx["nodes"][nid]
        who = dict(object_type="Host", obj=n.name, cluster=_cluster_name(ctx, n.cluster_id), node=n.name)
        if n.mem_total_bytes and alloc.get(nid):
            pct = alloc[nid] / n.mem_total_bytes * 100
            if pct >= HOST_MEM_ALLOC_WARN_PCT:
                add("warning", "Host memory overcommitted", detail=f"{pct:.0f}% of host memory is allocated to guests (warns at {HOST_MEM_ALLOC_WARN_PCT}%)", **who)
        if n.maintenance_mode:
            add("info", "Host in maintenance", detail=n.maintenance_reason or "Maintenance mode is on", since=n.maintenance_mode_since, **who)

    # Active findings, resolved to names and narrowed to the same scope
    narrowed = bool(scope.cluster_ids or scope.node_ids or scope.workload_ids)
    wl_by_id = {w.id: w for w in ctx["workloads"]}
    eff_clusters = {ctx["nodes"][i].cluster_id for i in ctx["node_ids"]}
    for f in db.query(Finding).filter(Finding.active.is_(True)).all():
        otype, obj, cluster, node, keep = "", None, None, None, not narrowed
        if f.object_type == "node" and f.object_id in ctx["nodes"]:
            n = ctx["nodes"][f.object_id]
            otype, obj, node, cluster, keep = "Host", n.name, n.name, _cluster_name(ctx, n.cluster_id), f.object_id in ctx["node_ids"]
        elif f.object_type == "workload":
            w = wl_by_id.get(f.object_id)
            if w is not None:
                ident = _wl_ident(ctx, w)
                otype, obj, node, cluster, keep = ident["type"], ident["vm"], ident["node"], ident["cluster"], True
            else:
                keep = not narrowed
        elif f.object_type == "cluster":
            c = ctx["clusters"].get(f.object_id)
            otype, obj, cluster = "Cluster", c.name if c else None, c.name if c else None
            keep = f.object_id in eff_clusters if narrowed else True
        elif f.object_type == "storage":
            st = db.get(Storage, f.object_id)
            if st is not None:
                nd = ctx["nodes"].get(st.node_id)
                cl = st.cluster_id or (nd.cluster_id if nd else None)
                otype, obj, cluster, node = "Storage", st.name, _cluster_name(ctx, cl), nd.name if nd else None
                keep = (st.node_id in ctx["node_ids"]) if st.node_id else (cl in eff_clusters if narrowed else True)
        elif f.object_type:
            otype = f.object_type.capitalize()
        if keep:
            sev = f.severity if f.severity in SEVERITY_RANK else "info"
            add(sev, f"Finding: {f.category}", otype, obj, cluster, node, f.title, source="PyXie finding", since=f.first_observed)

    rows.sort(key=lambda r: (SEVERITY_RANK.get(r["severity"], 3), r["cluster"] or "", r["node"] or "", (r["object"] or "").lower(), r["check"]))
    return rows


@dataclass
class Report:
    key: str
    label: str
    description: str
    columns: list
    build: Callable


REPORTS: dict[str, Report] = {
    r.key: r
    for r in [
        Report("pInfo", "pInfo", "VMs & containers — one row per guest", VINFO, build_vinfo),
        Report("pHost", "pHost", "Hosts (PVE nodes)", VHOST, build_vhost),
        Report("pStorage", "pStorage", "Storage — capacity, usage, and what sits on it", VSTORAGE, build_vstorage),
        Report("pDisk", "pDisk", "Virtual disks — one row per disk", VDISK, build_vdisk),
        Report("pNetwork", "pNetwork", "Virtual NICs — one row per adapter", VNETWORK, build_vnetwork),
        Report("pSnapshot", "pSnapshot", "Snapshots — one row per snapshot", VSNAPSHOT, build_vsnapshot),
        Report("pHealth", "pHealth", "Things that deserve a look — hygiene checks plus PyXie's active findings", VHEALTH, build_vhealth),
    ]
}


def last_collection(db: Session) -> dict:
    """Freshness of the detail data (disks/NICs/snapshots/IPs) for the UI."""
    run = db.query(ReportCollectionRun).order_by(ReportCollectionRun.started_at.desc()).first()
    done = (
        db.query(ReportCollectionRun)
        .filter(ReportCollectionRun.status.in_(["success", "partial"]))
        .order_by(ReportCollectionRun.ended_at.desc())
        .first()
    )
    stale_cutoff = datetime.now(timezone.utc).timestamp() - 1800
    running = bool(run and run.status == "running" and run.started_at.timestamp() > stale_cutoff)
    return {
        "running": running,
        "last_run": None
        if run is None
        else {
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
            "triggered_by": run.triggered_by,
            "workloads_total": run.workloads_total,
            "workloads_collected": run.workloads_collected,
            "workloads_failed": run.workloads_failed,
            "error": run.error,
        },
        "collected_at": done.ended_at.isoformat() if done and done.ended_at else None,
    }
