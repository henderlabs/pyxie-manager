"""Pure-function coverage for the per-host page shaping. No DB, no network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.node_detail import summarize_node_status, summarize_storage, trim_node_rrd

M501 = {
    "uptime": 24119, "cpu": 0.200296348705577, "wait": 0.0111319244687761, "loadavg": ["10.57", "11.53", "11.55"],
    "memory": {"used": 349148438528, "total": 810185592832}, "swap": {"used": 0, "total": 8589930496},
    "rootfs": {"used": 12189519872, "total": 100861726720},
    "cpuinfo": {"model": "Intel(R) Xeon(R) Gold 6126 CPU @ 2.60GHz", "cpus": 48, "cores": 24, "sockets": 2, "flags": "x" * 50},
    "boot-info": {"mode": "efi", "secureboot": 0}, "current-kernel": {"release": "7.0.14-20-pve"},
    "pveversion": "pve-manager/9.2.21/4f6e0ac86f9e8c7f", "ksm": {"shared": 0},
}


def test_node_status_numbers_and_units():
    s = summarize_node_status(M501)
    assert s["cpu_pct"] == 20.0 and s["io_delay_pct"] == 1.11
    assert s["loadavg"] == [10.57, 11.53, 11.55]
    assert s["mem_pct"] == 43.1 and s["root_pct"] == 12.1
    assert s["cpu_threads"] == 48 and s["cpu_sockets"] == 2 and "flags" not in s
    assert s["pve_version"] == "9.2.21" and s["kernel"] == "7.0.14-20-pve"
    assert s["boot_mode"] == "efi" and s["secure_boot"] is False


def test_node_status_missing_pieces_do_not_crash():
    s = summarize_node_status({})
    assert s["cpu_pct"] is None and s["mem_pct"] is None and s["loadavg"] == [] and s["secure_boot"] is None
    assert summarize_node_status(None) is None
    assert summarize_node_status({"pveversion": "weird"})["pve_version"] == "weird"


def test_storage_rows_and_ordering():
    rows = [
        {"storage": "local", "type": "dir", "active": 1, "shared": 0, "used": 10, "total": 100, "content": "iso,backup"},
        {"storage": "VMW-NFS01", "type": "nfs", "active": 1, "shared": 1, "used": 50, "total": 200, "content": "images"},
        {"storage": "dead", "type": "nfs", "active": 0, "shared": 1, "total": 0, "used": 0},
        {"storage": "a-local-lvm", "type": "lvmthin", "active": 1, "shared": 0, "used": 0, "total": 400},
    ]
    s = summarize_storage(rows)
    assert [x["name"] for x in s] == ["VMW-NFS01", "a-local-lvm", "local", "dead"]
    assert s[0]["used_pct"] == 25.0 and s[0]["shared"] is True and s[2]["content"] == ["iso", "backup"]
    assert s[3]["used_pct"] is None and s[3]["active"] is False
    assert summarize_storage(None) == []


def test_trim_node_rrd():
    rows = [{"time": 1, "cpu": 0.1, "junk": 1, "swapused": None}, {"cpu": 1}, "x", {"time": 2, "loadavg": 9.5}]
    assert trim_node_rrd(rows) == [{"time": 1, "cpu": 0.1}, {"time": 2, "loadavg": 9.5}]
    assert trim_node_rrd(None) == []
