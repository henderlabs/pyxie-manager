"""Enable the memory balloon device on VMs so PVE (and PyXie) can read their REAL
memory use. Run INSIDE the pyxie-manager-api container:

    docker cp ops/set_vm_balloon.py pyxie-manager-api:/tmp/
    docker exec pyxie-manager-api python /tmp/set_vm_balloon.py --vmids 215,1044 --fraction 0.5          # dry run
    docker exec pyxie-manager-api python /tmp/set_vm_balloon.py --vmids 215,1044 --fraction 0.5 --apply --actor you@example.com
    docker exec pyxie-manager-api python /tmp/set_vm_balloon.py --vmids 1078 --below-max-mb 2048 --apply --actor you@example.com   # databases: minimum = max - 2 GiB

The VM's `memory` (maximum) is left alone; `balloon` (the minimum) is set to
fraction x memory, or to memory minus --below-max-mb (keeps PVE from reclaiming
more than that much, e.g. for databases). Why: with `balloon: 0` PVE has no way to ask the guest what it
uses and reports the host-side process size (~100% for any VM that has touched its
RAM); with the device present it reports the guest's own figure (what Windows Task
Manager shows). The change is pending until the VM is next started or rebooted
through PVE; a running guest is not touched. Goes through PveMaintenanceClient, so
the PVE writes switch (Platform > Settings) must be on, and each change is written
to the audit log.
"""

import argparse
import sys

from pyxie_core.audit import write_audit_event
from pyxie_core.credentials import load_pve_credentials
from pyxie_core.db import SessionLocal
from pyxie_core.models import Cluster, Node, PveTarget, Workload
from pyxie_core.pve_write_client import PveMaintenanceClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vmids", required=True, help="comma-separated VMIDs")
    ap.add_argument("--fraction", type=float, default=0.5, help="balloon minimum as a fraction of memory (default 0.5)")
    ap.add_argument("--below-max-mb", type=int, default=None,
                    help="instead of --fraction: balloon minimum = memory minus this many MiB")
    ap.add_argument("--apply", action="store_true", help="make the change (default: dry run)")
    ap.add_argument("--actor", default="system", help="who is making the change, for the audit log")
    args = ap.parse_args()
    if not 0 < args.fraction <= 1:
        print("--fraction must be > 0 and <= 1", file=sys.stderr)
        return 2
    if args.below_max_mb is not None and args.below_max_mb < 0:
        print("--below-max-mb must be >= 0", file=sys.stderr)
        return 2

    db = SessionLocal()
    plans = []
    for vmid in [int(x) for x in args.vmids.split(",") if x.strip()]:
        w = db.query(Workload).filter(Workload.vmid == vmid, Workload.is_missing.is_(False), Workload.type == "vm").one_or_none()
        if w is None:
            print(f"vmid {vmid}: not found in inventory -- skipped")
            continue
        node = db.query(Node).filter(Node.id == w.node_id).one()
        cluster = db.query(Cluster).filter(Cluster.id == w.cluster_id).one()
        target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
        plans.append((w, node, target))

    if not plans:
        return 1

    applied = failed = 0
    with_clients = {}
    rule = f"memory minus {args.below_max_mb} MiB" if args.below_max_mb is not None else f"{args.fraction:g} x memory"
    print(f"{'DRY RUN' if not args.apply else 'APPLY'}: balloon minimum = {rule} (memory/maximum unchanged)\n")
    print(f"{'vm':<16}{'vmid':<7}{'node':<14}{'status':<9}{'memory MiB':<12}{'balloon now':<13}{'balloon new':<12}result")
    for w, node, target in plans:
        key = target.id
        if key not in with_clients:
            with_clients[key] = PveMaintenanceClient(load_pve_credentials(db, target, "maintenance"))
        client = with_clients[key]
        cfg = client._get(f"/nodes/{node.name}/qemu/{w.vmid}/config")
        memory_mb = int(cfg["memory"])
        current = cfg.get("balloon")
        if args.below_max_mb is not None:
            new = memory_mb - args.below_max_mb
            if new <= 0:
                print(f"{w.name}: --below-max-mb {args.below_max_mb} leaves no memory (max is {memory_mb} MiB) -- skipped")
                continue
        else:
            new = max(1, int(memory_mb * args.fraction))
        result = ""
        if current not in (None, 0) and int(current) == new:
            result = "already set -- no change"
        elif not args.apply:
            result = "would set"
        else:
            try:
                client.set_vm_balloon(node.name, w.vmid, balloon_mb=new)
                pending = client._get(f"/nodes/{node.name}/qemu/{w.vmid}/pending")
                got = next((p.get("pending") for p in pending if p.get("key") == "balloon"), None)
                if got is not None and int(got) == new:
                    result = f"SET (pending until power-cycle; PVE confirms pending balloon={got})"
                    applied += 1
                else:
                    result = f"sent, but PVE pending shows {got!r} -- check"
                    failed += 1
                write_audit_event(
                    db, event_category="workload", event_type="workload.balloon_configured",
                    actor=args.actor, actor_type="user" if "@" in args.actor else "system",
                    cluster_id=w.cluster_id, node_id=w.node_id, workload_id=w.id, operation="configure",
                    state_before={"balloon": current, "memory_mb": memory_mb},
                    state_after={"balloon": new, "memory_mb": memory_mb, "applies": "next VM start/PVE reboot"},
                    result="success" if got is not None and int(got) == new else "failure",
                    severity="info",
                    metadata={"summary": f"{w.name}: balloon minimum {current} -> {new} MiB (maximum {memory_mb} MiB); pending until power-cycle"},
                    commit=True,
                )
            except Exception as exc:  # noqa: BLE001
                result = f"FAILED: {exc}"
                failed += 1
        print(f"{(w.name or '')[:15]:<16}{w.vmid:<7}{node.name.replace('pve-slc-', ''):<14}{w.status:<9}{memory_mb:<12}{str(current):<13}{new:<12}{result}")

    for c in with_clients.values():
        c.close()
    db.close()
    if args.apply:
        print(f"\napplied: {applied}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
