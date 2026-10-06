"""Shaping PVE node data for the per-host page. Pure functions: no network, no DB."""
from __future__ import annotations

from typing import Any, Iterable

# rrddata keys the host charts use; everything else PVE returns is dropped.
NODE_RRD_KEYS = (
    "time", "cpu", "iowait", "loadavg", "maxcpu",
    "memused", "memtotal", "memavailable", "arcsize",
    "netin", "netout", "swapused", "swaptotal", "rootused", "roottotal",
    "pressurecpusome", "pressureiosome", "pressureiofull", "pressurememorysome",
)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(used: Any, total: Any) -> float | None:
    u, t = _num(used), _num(total)
    return round(u / t * 100, 1) if u is not None and t else None


def summarize_node_status(status: dict | None) -> dict | None:
    """`/nodes/<n>/status` reduced to what the page shows."""
    if status is None:
        return None
    mem = status.get("memory") or {}
    swap = status.get("swap") or {}
    root = status.get("rootfs") or {}
    cpuinfo = status.get("cpuinfo") or {}
    boot = status.get("boot-info") or {}
    kernel = status.get("current-kernel") or {}
    pve = str(status.get("pveversion") or "")
    load = [_num(x) for x in (status.get("loadavg") or [])]
    return {
        "uptime": status.get("uptime"),
        "cpu_pct": round(_num(status.get("cpu")) * 100, 1) if _num(status.get("cpu")) is not None else None,
        "io_delay_pct": round(_num(status.get("wait")) * 100, 2) if _num(status.get("wait")) is not None else None,
        "loadavg": load,
        "cpu_model": cpuinfo.get("model"),
        "cpu_threads": cpuinfo.get("cpus"),
        "cpu_cores": cpuinfo.get("cores"),
        "cpu_sockets": cpuinfo.get("sockets"),
        "mem_used": mem.get("used"),
        "mem_total": mem.get("total"),
        "mem_pct": _pct(mem.get("used"), mem.get("total")),
        "swap_used": swap.get("used"),
        "swap_total": swap.get("total"),
        "root_used": root.get("used"),
        "root_total": root.get("total"),
        "root_pct": _pct(root.get("used"), root.get("total")),
        "kernel": kernel.get("release"),
        "pve_version": pve.split("/", 1)[1].split("/", 1)[0] if pve.startswith("pve-manager/") else (pve or None),
        "boot_mode": boot.get("mode"),
        "secure_boot": bool(boot.get("secureboot")) if "secureboot" in boot else None,
        "ksm_shared": (status.get("ksm") or {}).get("shared"),
    }


def summarize_storage(rows: Iterable[dict] | None) -> list[dict]:
    """`/nodes/<n>/storage` as the page's 'Storage on this host' rows, usable ones first."""
    out = []
    for r in rows or []:
        total = _num(r.get("total"))
        out.append({
            "name": r.get("storage"),
            "type": r.get("type"),
            "shared": bool(r.get("shared")),
            "active": bool(r.get("active")),
            "enabled": bool(r.get("enabled", 1)),
            "content": [c for c in str(r.get("content") or "").split(",") if c],
            "used": r.get("used"),
            "total": r.get("total"),
            "used_pct": _pct(r.get("used"), total) if total else None,
        })
    out.sort(key=lambda s: (not s["active"], not s["shared"], str(s["name"]).lower()))
    return out


def trim_node_rrd(rows: Iterable[dict] | None) -> list[dict]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("time") is None:
            continue
        out.append({k: row[k] for k in NODE_RRD_KEYS if k in row and row[k] is not None})
    return out
