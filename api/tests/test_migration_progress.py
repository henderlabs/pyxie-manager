"""Progress parsing for a live migration with node-local disks: storage mirror first, then VM memory (real PVE log lines)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from pyxie_core.pve_write_client import update_migration_progress


def L(n, t):
    return {"n": n, "t": t}


def test_storage_phase_then_vm_phase():
    p = update_migration_progress(None, [L(1, "starting migration of VM 11170"), L(11, "mirror-scsi0: transferred 0.0 B of 100.0 GiB (0.00%) in 0s")])
    assert p["phase"] == "storage" and p["storage"]["pct"] == 0.0
    p = update_migration_progress(p, [L(72, "mirror-scsi0: transferred 10.6 GiB of 100.0 GiB (10.59%) in 1m 1s")])
    assert p["phase"] == "storage" and p["pct"] == 10.6 and p["storage"]["rate_bytes_per_sec"] > 100 * 1024**2 // 10 and "vm" not in p
    p = update_migration_progress(p, [L(569, "mirror-scsi0: transferred 100.0 GiB of 100.0 GiB (100.00%) in 9m 19s, ready"), L(574, "starting online/live migration"), L(579, "start migrate command to unix:/x")])
    assert p["storage"]["done"] and p["storage"]["pct"] == 100.0 and p["phase"] == "storage"
    p = update_migration_progress(p, [L(580, "migration active, transferred 94.8 MiB of 10.0 GiB VM-state, 106.6 MiB/s")])
    assert p["phase"] == "vm" and p["vm"]["pct"] == 0.9 and p["storage"]["done"] and p["pct"] == 0.9


def test_two_drives_are_summed_and_plain_vm_migration_has_no_storage():
    p = update_migration_progress(None, [
        L(1, "mirror-scsi0: transferred 50.0 GiB of 100.0 GiB (50.00%) in 5m"),
        L(2, "mirror-scsi1: transferred 25.0 GiB of 50.0 GiB (50.00%) in 4m 30s"),
    ])
    assert p["storage"]["total_bytes"] == 150 * 1024**3 and p["storage"]["pct"] == 50.0
    q = update_migration_progress(None, [L(1, "migration active, transferred 1.0 GiB of 4.0 GiB VM-state, 100.0 MiB/s")])
    assert q["phase"] == "vm" and "storage" not in q
    assert update_migration_progress(None, [L(1, "conntrack state migration not supported")]) is None
