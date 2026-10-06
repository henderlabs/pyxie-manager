"""Shaping PVE data for the single-workload page.

Pure functions only (no network, no DB) so they are easy to test: the router
fetches `status/current`, the VM/CT config and rrddata through the inventory
client, then hands the raw dicts here.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .vm_liveness import assess_liveness

RRD_TIMEFRAMES = ("hour", "day", "week", "month", "year")

# rrddata keys the page charts. Anything else PVE returns is dropped so the
# payload stays small and the browser never sees fields we did not choose.
RRD_KEYS = (
    "time",
    "cpu",
    "maxcpu",
    "mem",
    "maxmem",
    "memhost",
    "netin",
    "netout",
    "diskread",
    "diskwrite",
    "pressurecpusome",
    "pressurecpufull",
    "pressureiosome",
    "pressureiofull",
    "pressurememorysome",
    "pressurememoryfull",
)

_QEMU_DISK_KEY = re.compile(r"^(scsi|virtio|sata|ide)\d+$")
_LXC_MOUNT_KEY = re.compile(r"^(rootfs|mp\d+)$")
_NET_KEY = re.compile(r"^net\d+$")
_TRUE = ("1", "yes", "true", "on")


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in _TRUE


def _kv(text: str) -> tuple[str, dict[str, str]]:
    """'local-lvm:vm-1-disk-0,size=32G,ssd=1' -> ('local-lvm:vm-1-disk-0', {...})."""
    head, _, rest = str(text).partition(",")
    opts: dict[str, str] = {}
    for part in rest.split(",") if rest else []:
        k, _, v = part.partition("=")
        if k:
            opts[k.strip()] = v.strip()
    return head.strip(), opts


def _storage_of(volume: str) -> str | None:
    if ":" not in volume:
        return None
    return volume.split(":", 1)[0] or None


def parse_disks(config: dict, *, lxc: bool = False) -> list[dict]:
    out: list[dict] = []
    for key in sorted(config, key=_natural_key):
        if lxc:
            if not _LXC_MOUNT_KEY.match(key):
                continue
        elif key.startswith(("efidisk", "tpmstate")):
            pass
        elif not _QEMU_DISK_KEY.match(key):
            continue
        volume, opts = _kv(config[key])
        is_cdrom = opts.get("media") == "cdrom"
        out.append(
            {
                "key": key,
                "volume": volume,
                "storage": _storage_of(volume),
                "size": opts.get("size"),
                "kind": "cdrom" if is_cdrom else ("efi" if key.startswith("efidisk") else "tpm" if key.startswith("tpmstate") else "disk"),
                "options": {k: v for k, v in opts.items() if k not in ("size", "media")},
                "empty": volume in ("none", ""),
            }
        )
    return out


def parse_nets(config: dict, *, lxc: bool = False) -> list[dict]:
    out: list[dict] = []
    for key in sorted(config, key=_natural_key):
        if not _NET_KEY.match(key):
            continue
        _, opts = _kv(config[key])
        # QEMU: 'virtio=BC:24:..,bridge=vmbr0'; LXC: 'name=eth0,bridge=vmbr0,hwaddr=...,ip=dhcp'
        first = str(config[key]).split(",", 1)[0]
        model, _, mac = first.partition("=")
        out.append(
            {
                "key": key,
                "model": None if lxc else model,
                "mac": opts.get("hwaddr") if lxc else mac or None,
                "bridge": opts.get("bridge"),
                "vlan_tag": opts.get("tag"),
                "firewall": _truthy(opts.get("firewall")),
                "rate": opts.get("rate"),
                "ip": opts.get("ip"),
            }
        )
    return out


def _natural_key(key: str):
    m = re.match(r"^([a-z]+)(\d*)$", key)
    return (m.group(1), int(m.group(2) or 0)) if m else (key, 0)


def summarize_config(config: dict | None, *, lxc: bool = False) -> dict | None:
    """The facts the Summary and Hardware tabs show, normalised."""
    if config is None:
        return None
    cfg = config
    agent_raw = str(cfg.get("agent", ""))
    agent_enabled = agent_raw.split(",", 1)[0].strip().lower() in _TRUE or "enabled=1" in agent_raw
    memory_mb = _int(cfg.get("memory"))
    sockets = _int(cfg.get("sockets")) or 1
    cores = _int(cfg.get("cores"))
    summary: dict[str, Any] = {
        "start_at_boot": _truthy(cfg.get("onboot")),
        "protection": _truthy(cfg.get("protection")),
        "description": cfg.get("description"),
        "tags": [t for t in re.split(r"[;,]", str(cfg.get("tags", ""))) if t.strip()],
        "lock": cfg.get("lock"),
        "ostype": cfg.get("ostype"),
        "memory_mb": memory_mb,
        "balloon_min_mb": _int(cfg.get("balloon")),
        "cores": cores,
        "sockets": sockets,
        "vcpus": (cores or 0) * sockets or None,
        "cpu_type": cfg.get("cpu"),
        "disks": parse_disks(cfg, lxc=lxc),
        "nets": parse_nets(cfg, lxc=lxc),
    }
    if lxc:
        summary.update({"hostname": cfg.get("hostname"), "unprivileged": _truthy(cfg.get("unprivileged")), "swap_mb": _int(cfg.get("swap")), "features": cfg.get("features")})
    else:
        summary.update(
            {
                "agent_enabled": agent_enabled,
                "bios": cfg.get("bios") or "seabios",
                "machine": cfg.get("machine"),
                "scsihw": cfg.get("scsihw"),
                "boot": cfg.get("boot"),
                "numa": _truthy(cfg.get("numa")),
            }
        )
    return summary


def _int(value: Any) -> int | None:
    try:
        return int(str(value).split(",", 1)[0])
    except (TypeError, ValueError):
        return None


def summarize_status(status: dict | None) -> dict | None:
    """Live `status/current` reduced to what the page shows."""
    if status is None:
        return None
    keys = (
        "status", "qmpstatus", "uptime", "cpu", "cpus", "mem", "maxmem", "balloon",
        "netin", "netout", "diskread", "diskwrite", "disk", "maxdisk", "lock",
        "running-qemu", "running-machine", "pid",
        "pressurecpusome", "pressurecpufull", "pressureiosome", "pressureiofull",
        "pressurememorysome", "pressurememoryfull",
    )
    out = {k: status.get(k) for k in keys if k in status}
    ha = status.get("ha")
    if isinstance(ha, dict):
        out["ha"] = {"managed": bool(ha.get("managed")), "state": ha.get("state"), "group": ha.get("group")}
    info = status.get("ballooninfo")
    if isinstance(info, dict):
        out["ballooninfo"] = {k: info.get(k) for k in ("actual", "free_mem", "total_mem", "max_mem", "mem_swapped_in", "mem_swapped_out") if k in info}
    return out


def live_check(status: dict | None, expected_status: str | None, elapsed: float = 0.0, error: str | None = None, *, lxc: bool = False) -> dict:
    """The workload page's liveness badge: {state: ok|warn|bad|info|unknown, detail}.

    `bad` = QEMU is not answering (or reports a fault); see pyxie_core.vm_liveness."""
    if lxc:  # a container has no QEMU control socket to probe
        if status is None:
            return {"state": "unknown", "detail": f"Could not ask PVE: {(error or 'no status')[:120]}"}
        pve_status = status.get("status")
        if expected_status and pve_status and expected_status != pve_status:
            return {"state": "info", "detail": f"PyXie last saw '{expected_status}', PVE now says '{pve_status}'"}
        return {"state": "ok", "detail": f"PVE status '{pve_status}'"}
    a = assess_liveness(status, elapsed, error)
    state = a["state"]
    if state == "unknown":
        return {"state": "unknown", "detail": a["detail"]}
    if state in ("unresponsive", "problem"):
        return {"state": "bad", "detail": a["detail"]}
    if state == "slow":
        return {"state": "warn", "detail": a["detail"]}
    pve_status = (status or {}).get("status")
    if expected_status and pve_status and expected_status != pve_status:
        return {"state": "info", "detail": f"PyXie last saw '{expected_status}', PVE now says '{pve_status}'"}
    return {"state": "ok", "detail": a["detail"]}


def trim_rrd(rows: Iterable[dict] | None) -> list[dict]:
    """Keep only charted keys; drop rows with no timestamp."""
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("time") is None:
            continue
        out.append({k: row[k] for k in RRD_KEYS if k in row and row[k] is not None})
    return out


def tasks_for_vmid(tasks: Iterable[Any], vmid: int, limit: int = 50) -> list[Any]:
    """Filter task rows to those whose UPID names this guest.

    UPID layout: UPID:node:pid:pstart:starttime:type:id:user: -- the guest id is
    field 6. Matching the field (not a substring) keeps vmid 100 out of 1100.
    """
    want = str(vmid)
    out = []
    for t in tasks:
        parts = str(getattr(t, "upid", "")).split(":")
        if len(parts) > 6 and parts[6] == want:
            out.append(t)
            if len(out) >= limit:
                break
    return out


# Interfaces a guest creates for its own containers/virtualisation; their addresses are
# not how anyone reaches the guest, so the page leaves them out of "IP addresses".
_NOISE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "cilium", "tun", "weave", "lxc")


def is_noise_iface(name: str | None) -> bool:
    n = (name or "").lower()
    return n == "lo" or any(n.startswith(p) for p in _NOISE_PREFIXES[1:])


def guest_addresses(ifaces: Iterable[dict] | None) -> list[dict]:
    """Reachable addresses from guest-agent (QEMU) or /interfaces (LXC) output."""
    out: list[dict] = []
    for i in ifaces or []:
        name = i.get("name")
        if is_noise_iface(name):
            continue
        if i.get("ip-addresses") is not None:  # QEMU agent shape
            candidates = [(a.get("ip-address"), i.get("hardware-address")) for a in i["ip-addresses"]]
        else:  # LXC shape
            candidates = [(str(i[k]).split("/")[0], i.get("hwaddr")) for k in ("inet", "inet6") if i.get(k)]
        for addr, mac in candidates:
            if addr and not str(addr).lower().startswith("fe80"):
                out.append({"iface": name, "address": addr, "mac": mac})
    return out


_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size_bytes(text: Any) -> int | None:
    """PVE disk sizes: '32G', '528K', '4M', '1T'; a bare number is bytes."""
    t = str(text or "").strip().upper()
    if not t:
        return None
    try:
        if t[-1] in _SIZE_UNITS:
            return int(float(t[:-1]) * _SIZE_UNITS[t[-1]])
        return int(float(t))
    except ValueError:
        return None


def facts_from_config(config: dict | None, *, lxc: bool = False) -> dict | None:
    """The per-VM facts the Workloads table columns need, from one guest config."""
    summary = summarize_config(config, lxc=lxc)
    if summary is None:
        return None
    disks = [d for d in summary["disks"] if d["kind"] == "disk" and not d["empty"]]
    sizes = [parse_size_bytes(d["size"]) for d in disks]
    return {
        "start_at_boot": summary["start_at_boot"],
        "agent_enabled": None if lxc else summary.get("agent_enabled"),
        "ostype": summary["ostype"],
        "disk_bytes": sum(x for x in sizes if x) if any(sizes) else None,
        "storages": sorted({d["storage"] for d in disks if d["storage"]}),
        "protection": summary["protection"],
    }
