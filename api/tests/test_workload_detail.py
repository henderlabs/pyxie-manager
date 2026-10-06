"""Pure-function coverage for the single-workload page shaping. No DB, no network."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.workload_detail import (
    facts_from_config,
    parse_size_bytes,
    guest_addresses,
    is_noise_iface,
    live_check,
    parse_disks,
    parse_nets,
    summarize_config,
    summarize_status,
    tasks_for_vmid,
    trim_rrd,
)

VM104 = {
    "scsi0": "VMW-NFS01:104/vm-104-disk-1.qcow2,discard=on,iothread=1,size=32G,ssd=1",
    "bios": "ovmf", "cores": 2, "machine": "q35", "sockets": 1, "agent": "1", "cpu": "x86-64-v2-AES",
    "efidisk0": "VMW-NFS01:104/vm-104-disk-0.qcow2,efitype=4m,size=528K",
    "tpmstate0": "VMW-NFS01:104/vm-104-disk-2.raw,size=4M,version=v2.0",
    "ide2": "none,media=cdrom", "memory": "4096", "onboot": 1, "tags": "prod;sql",
    "net0": "virtio=BC:24:11:39:00:74,bridge=dev1356,firewall=1,queues=4,tag=56",
    "net1": "e1000=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
}


def test_disks_kinds_storage_and_cdrom():
    disks = {d["key"]: d for d in parse_disks(VM104)}
    assert disks["scsi0"]["storage"] == "VMW-NFS01" and disks["scsi0"]["size"] == "32G" and disks["scsi0"]["kind"] == "disk"
    assert disks["efidisk0"]["kind"] == "efi" and disks["tpmstate0"]["kind"] == "tpm"
    assert disks["ide2"]["kind"] == "cdrom" and disks["ide2"]["empty"] is True


def test_disks_sorted_naturally():
    cfg = {"scsi10": "s:a,size=1G", "scsi2": "s:b,size=1G"}
    assert [d["key"] for d in parse_disks(cfg)] == ["scsi2", "scsi10"]


def test_nets_qemu_and_lxc():
    nets = {n["key"]: n for n in parse_nets(VM104)}
    assert nets["net0"]["model"] == "virtio" and nets["net0"]["mac"] == "BC:24:11:39:00:74"
    assert nets["net0"]["bridge"] == "dev1356" and nets["net0"]["vlan_tag"] == "56" and nets["net0"]["firewall"] is True
    assert nets["net1"]["firewall"] is False
    lxc = parse_nets({"net0": "name=eth0,bridge=vmbr1,hwaddr=AA:BB:CC:00:00:01,ip=dhcp"}, lxc=True)[0]
    assert lxc["mac"] == "AA:BB:CC:00:00:01" and lxc["ip"] == "dhcp" and lxc["model"] is None


def test_summary_start_at_boot_agent_and_defaults():
    s = summarize_config(VM104)
    assert s["start_at_boot"] is True and s["agent_enabled"] is True and s["bios"] == "ovmf"
    assert s["vcpus"] == 2 and s["memory_mb"] == 4096 and s["tags"] == ["prod", "sql"]
    off = summarize_config({"memory": "1024", "cores": "4", "sockets": "2", "agent": "enabled=0"})
    assert off["start_at_boot"] is False and off["agent_enabled"] is False and off["vcpus"] == 8 and off["bios"] == "seabios"
    assert summarize_config(None) is None
    assert summarize_config({"agent": "enabled=1,fstrim_cloned_disks=1"})["agent_enabled"] is True


def test_summary_lxc_has_no_qemu_fields():
    s = summarize_config({"rootfs": "local-lvm:vm-9-disk-0,size=8G", "hostname": "ct9", "unprivileged": 1, "memory": 512}, lxc=True)
    assert s["hostname"] == "ct9" and s["unprivileged"] is True and "bios" not in s
    assert s["disks"][0]["key"] == "rootfs" and s["disks"][0]["size"] == "8G"


def test_status_trims_and_normalises_ha():
    raw = {"status": "running", "qmpstatus": "running", "uptime": 5, "secret": "x", "ha": {"managed": 0},
           "ballooninfo": {"actual": 1, "free_mem": 2, "junk": 3}}
    s = summarize_status(raw)
    assert "secret" not in s and s["ha"] == {"managed": False, "state": None, "group": None}
    assert s["ballooninfo"] == {"actual": 1, "free_mem": 2}
    assert summarize_status(None) is None


def test_live_check_cases():
    assert live_check(None, "running")["state"] == "unknown"
    assert live_check({"status": "running", "qmpstatus": "running", "uptime": 9}, "running")["state"] == "ok"
    assert live_check({"status": "running", "qmpstatus": "internal-error", "uptime": 9}, "running")["state"] == "warn"
    assert live_check({"status": "running"}, "running")["state"] == "warn"
    assert live_check({"status": "stopped", "qmpstatus": "stopped"}, "running")["state"] == "info"
    assert live_check({"status": "stopped", "qmpstatus": "stopped"}, "stopped")["state"] == "ok"


def test_trim_rrd_keeps_only_charted_keys_and_timestamped_rows():
    rows = [{"time": 1, "cpu": 0.5, "junk": 9, "mem": None}, {"cpu": 1}, "bad", {"time": 2, "netin": 3}]
    assert trim_rrd(rows) == [{"time": 1, "cpu": 0.5}, {"time": 2, "netin": 3}]
    assert trim_rrd(None) == []


def test_tasks_match_vmid_field_not_substring():
    mk = lambda upid: SimpleNamespace(upid=upid)
    t100 = mk("UPID:n1:0001:0002:0003:qmstart:100:root@pam:")
    t1100 = mk("UPID:n1:0001:0002:0003:qmstart:1100:root@pam:")
    thex = mk("UPID:n1:0100:0100:0100:vzdump::root@pam:")
    t100b = mk("UPID:n2:0001:0002:0003:qmigrate:100:u@pve:")
    assert tasks_for_vmid([t1100, t100, thex, t100b], 100) == [t100, t100b]
    assert len(tasks_for_vmid([t100] * 5, 100, limit=2)) == 2


def test_guest_addresses_drop_loopback_docker_bridge_and_linklocal():
    ifaces = [
        {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1"}]},
        {"name": "enp6s18", "hardware-address": "bc:24", "ip-addresses": [{"ip-address": "10.55.66.201"}, {"ip-address": "fe80::1"}]},
        {"name": "docker0", "ip-addresses": [{"ip-address": "172.17.0.1"}]},
        {"name": "br-e84d0308353f", "ip-addresses": [{"ip-address": "192.168.250.1"}]},
        {"name": "veth123", "ip-addresses": [{"ip-address": "10.0.0.9"}]},
    ]
    assert guest_addresses(ifaces) == [{"iface": "enp6s18", "address": "10.55.66.201", "mac": "bc:24"}]
    lxc = [{"name": "eth0", "hwaddr": "aa", "inet": "10.1.2.3/24", "inet6": "fe80::2/64"}, {"name": "lo", "inet": "127.0.0.1/8"}]
    assert guest_addresses(lxc) == [{"iface": "eth0", "address": "10.1.2.3", "mac": "aa"}]
    assert guest_addresses(None) == []
    assert is_noise_iface("Docker0") and not is_noise_iface("ens192") and not is_noise_iface("bridge0")


def test_parse_size_bytes():
    assert parse_size_bytes("32G") == 32 * 1024**3 and parse_size_bytes("528K") == 528 * 1024
    assert parse_size_bytes("1.5T") == int(1.5 * 1024**4) and parse_size_bytes("4096") == 4096
    assert parse_size_bytes("") is None and parse_size_bytes(None) is None and parse_size_bytes("abc") is None


def test_facts_from_config_counts_only_real_disks():
    f = facts_from_config(VM104)
    assert f["start_at_boot"] is True and f["agent_enabled"] is True and f["protection"] is False
    assert f["disk_bytes"] == 32 * 1024**3  # scsi0 only: not the EFI disk, TPM state or empty CD-ROM
    assert f["storages"] == ["VMW-NFS01"]
    two = facts_from_config({"scsi0": "a:x,size=10G", "scsi1": "b:y,size=20G", "memory": "1024"})
    assert two["disk_bytes"] == 30 * 1024**3 and two["storages"] == ["a", "b"] and two["start_at_boot"] is False
    nodisk = facts_from_config({"memory": "512", "ide2": "none,media=cdrom"})
    assert nodisk["disk_bytes"] is None and nodisk["storages"] == []
    ct = facts_from_config({"rootfs": "local-lvm:vm-9-disk-0,size=8G", "onboot": 1}, lxc=True)
    assert ct["agent_enabled"] is None and ct["start_at_boot"] is True and ct["disk_bytes"] == 8 * 1024**3
    assert facts_from_config(None) is None
