"""Precondition revalidation -- re-reads LIVE PVE state (never the DB cache,
which may be up to one polling interval stale) immediately before an
operation is allowed to execute.

This exists because a maintenance plan or dry-run result is guidance, not
truth at execution time: something else (a human on the PVE console, PVE
itself, another tool) may have changed the cluster since the plan was
generated or since approval was granted. "Current reality always wins" is
the rule for every stage of every write-capable workflow in this app.

Uses the existing read-only PveClient (all GET) -- revalidation itself
never needs write access.
"""

from dataclasses import dataclass, field
from typing import Optional

from .pve_client import PveClient


class PreconditionFailed(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class MigrationPrecondition:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


def revalidate_live_migration(
    client: PveClient,
    *,
    source_node: str,
    vmid: int,
    target_node: str,
    expected_memory_bytes: Optional[int],
    require_running: bool = True,
) -> MigrationPrecondition:
    reasons: list[str] = []
    snapshot: dict = {}

    try:
        cluster_status = client.cluster_status()
    except Exception as exc:
        return MigrationPrecondition(ok=False, reasons=[f"could not read live cluster status: {exc}"])
    snapshot["cluster_status"] = cluster_status

    node_entries = {e["name"]: e for e in cluster_status if e.get("type") == "node"}
    total_nodes = len(node_entries)
    online_nodes = [n for n, e in node_entries.items() if e.get("online")]
    quorate_entry = next((e for e in cluster_status if e.get("type") == "cluster"), None)
    if quorate_entry is not None and not quorate_entry.get("quorate"):
        reasons.append("cluster is not quorate right now")

    if target_node not in node_entries or not node_entries[target_node].get("online"):
        reasons.append(f"destination node {target_node} is not online right now")

    if source_node not in node_entries or not node_entries[source_node].get("online"):
        reasons.append(f"source node {source_node} is not online right now")

    # confirm the VM is still where PyXie's plan thinks it is, and running
    try:
        qemu_list = client.qemu_list(source_node)
    except Exception as exc:
        reasons.append(f"could not read live VM list on {source_node}: {exc}")
        qemu_list = []
    live_vm = next((v for v in qemu_list if int(v.get("vmid", -1)) == vmid), None)
    snapshot["source_qemu_entry"] = live_vm
    if live_vm is None:
        reasons.append(f"VM {vmid} is no longer listed on {source_node} (moved or removed since plan/approval)")
    elif require_running and live_vm.get("status") != "running":
        # Only enforced when the plan expects the guest to still be running
        # right now -- an offline migration of a guest that was ALREADY
        # stopped at dry-run time has nothing to revalidate here; the guest
        # not running is the expected, unremarkable case for it.
        reasons.append(f"VM {vmid} is no longer 'running' (status={live_vm.get('status')})")

    # destination headroom, re-read live rather than from the DB's cached workload rows
    try:
        dest_status = client.node_status(target_node)
    except Exception as exc:
        reasons.append(f"could not read live status for {target_node}: {exc}")
        dest_status = {}
    snapshot["destination_status"] = dest_status
    if expected_memory_bytes and dest_status:
        mem = dest_status.get("memory") or {}
        total = mem.get("total")
        used = mem.get("used")
        if total is not None and used is not None:
            headroom = total - used
            if headroom < expected_memory_bytes:
                reasons.append(
                    f"destination {target_node} no longer has sufficient headroom "
                    f"({headroom} bytes free, needs {expected_memory_bytes})"
                )

    # No other active task already touching this VM. source="active" +
    # vmid=<vmid> are PVE's own server-side filters (confirmed against the
    # official API: `pvesh usage /nodes/{node}/tasks`) -- the previous
    # version called this with PVE's default source ('archive', finished
    # tasks only) and then looked for status=='running' client-side, which
    # can never match anything in an archive listing, and used a raw
    # substring check on the task id that would false-positive (e.g. vmid
    # 100 matching inside a task for vmid 1100). Exact server-side vmid filtering
    # avoids both problems at once.
    try:
        conflicting = client.tasks(source_node, limit=50, source="active", vmid=vmid) or []
    except Exception:
        conflicting = []
    if conflicting:
        reasons.append(f"VM {vmid} already has an active PVE task in progress: {conflicting[0].get('upid')}")

    return MigrationPrecondition(ok=(len(reasons) == 0), reasons=reasons, snapshot=snapshot)
