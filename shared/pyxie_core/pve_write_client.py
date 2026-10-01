"""The ONLY module in this codebase allowed to issue a write request against
PVE. Deliberately separate from pve_client.py (which stays GET-only, per its
own module docstring) rather than adding write methods to that class -- so
"grep for PveWriteClient usages" is always a complete answer to "what can
mutate PVE from PyXie."

Two independent guardrails, both required, matching the Safety Contract:
  1. The PVE_MUTATIONS_ENABLED environment variable must be exactly "true".
     This is a global kill switch checked on every write call, not just at
     startup -- it stays effective even if some future code path constructs
     this client without going through the operations engine.
  2. The credential used must come from the 'maintenance' slot, never
     'inventory' -- callers pass PveCredentials the same as the read-only
     client, but discovery.build_pve_client() (which always loads
     'inventory') is never used here; see operations_engine credential
     loading in migration_workflow.py.

Every write method returns PVE's UPID and nothing else -- it is the
caller's job (see preconditions.py + migration_workflow.py) to monitor the
task to completion and independently read state back. A UPID is a
submission receipt, not a success signal.
"""

import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .db import SessionLocal
from .models import AppSettings
from .pve_client import PveAuthError, PveConnectionError, PveCredentials, PveTlsError, _EndpointPool


class MutationsDisabledError(Exception):
    """Raised when PVE_MUTATIONS_ENABLED is not exactly 'true'."""


class TaskTimeoutError(Exception):
    """Raised when a PVE task does not reach a terminal state within timeout."""


class SelfProtectionError(Exception):
    """Raised when a write would power off or force-stop the VM PyXie
    Manager itself runs on. See _guard_self_vmid below."""


def _mutations_enabled() -> bool:
    """Re-checked fresh on every write call, not cached -- was previously
    an env var (a redeploy-to-change kill switch); now a Settings-page
    toggle (Platform > Settings), backed by app_settings.id=1 so it stays
    a real, live-updatable control instead of the routine settings.changed
    audit event covering it -- see settings.pve_mutations_enabled_changed
    for the dedicated one. Opens its own short-lived session rather than
    threading a `db` parameter through every write method on this class
    (and every caller of every one of them) -- this is a single cheap
    read, not worth that blast radius."""
    db = SessionLocal()
    try:
        return bool(db.query(AppSettings.pve_mutations_enabled).filter(AppSettings.id == 1).scalar())
    finally:
        db.close()


def _disabled_message(action: str) -> str:
    return f"PVE writes are disabled -- refusing to {action}. Enable them in Platform → Settings."


def self_vmid_from_env() -> Optional[str]:
    """The VMID PyXie Manager's own API/worker processes run on, if
    configured. Unset means self-protection can't be evaluated -- treated
    as "not this VM" rather than blocking everything, since an unset value
    is far more likely to mean "not deployed as a guest yet" than "someone
    forgot to protect it." Set PYXIE_SELF_VMID in .env when PyXie itself
    runs as a PVE guest, and keep it current if that guest is ever
    recreated with a new VMID."""
    value = os.environ.get("PYXIE_SELF_VMID", "").strip()
    return value or None


def _guard_self_vmid(vmid: int, action: str) -> None:
    """Unconditional hard block -- no override exists yet. Fixes a real
    near-incident: the VM running PyXie Manager itself landed in an
    approved maintenance plan's shutdown bucket during a real evacuation,
    caught only because a human was watching and cancelled it before it
    executed; nothing in the system itself would have refused it. This is
    that missing hard block. Placed here in PveMaintenanceClient -- the
    single write chokepoint for every PVE mutation -- so it covers
    shutdown_vm and force_stop_vm (and therefore resize's power-cycle and
    offline migration's shutdown step too) regardless of which workflow
    called them, without needing every caller to remember to check
    separately.

    NOT applied to reboot_vm or migrate_vm. Migrating this VM (while it
    stays running) is how the real incident was actually resolved safely
    -- moving it to another node, not blocking all movement of it.
    Rebooting it is deliberately exempt too: PVE's own reboot task
    guarantees the power-back-on itself, independent of PyXie's own
    worker process surviving -- see reboot_vm's own docstring for the
    full reasoning. Only shutdown and force-stop leave this VM with
    nothing guaranteed to bring it back, which is the actual failure
    mode this guard exists to prevent."""
    self_vmid = self_vmid_from_env()
    if self_vmid is not None and str(vmid) == self_vmid:
        raise SelfProtectionError(
            f"refusing to {action} vmid {vmid} -- PYXIE_SELF_VMID identifies this as the VM running "
            f"PyXie Manager itself; doing this here would strand the very process executing this "
            f"operation. No override path exists yet; this requires a deliberate code change or "
            f"manual action outside PyXie, not a UI toggle."
        )


def _parse_net_string(net: str) -> list[tuple[str, str]]:
    """Split a PVE net-device string into ordered key/value pairs. QEMU's
    leading token has no explicit key -- it's `<model>=<macaddr>`, e.g.
    'virtio=BC:24:11:4A:20:1C' -- but that parses fine under this same
    generic key=value split (key ends up 'virtio', value the MAC); no
    special-casing needed. LXC's leading token ('name=eth0') was always a
    real key to begin with, so both guest types parse identically."""
    pairs = []
    for part in net.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        pairs.append((key, value))
    return pairs


def _set_net_tag(net: str, tag: Optional[int]) -> str:
    """Return `net` with its `tag` parameter set to `tag` (or removed
    entirely if `tag` is None, meaning untagged/native VLAN on that
    bridge), every other parameter and their original order preserved."""
    pairs = [(k, v) for k, v in _parse_net_string(net) if k != "tag"]
    if tag is not None:
        pairs.append(("tag", str(tag)))
    return ",".join(f"{k}={v}" for k, v in pairs)


# Matches PVE's own qmigrate task log lines, e.g.:
#   "migration active, transferred 405.4 MiB of 8.0 GiB VM-state, 101.0 MiB/s"
# PVE's own units vary (MiB/GiB/KiB) so this is unit-aware, not byte-hardcoded.
_MIGRATE_PROGRESS_RE = re.compile(
    r"migration active, transferred ([\d.]+) (\wi?B) of ([\d.]+) (\wi?B) VM-state(?:, ([\d.]+) (\wi?B)/s)?"
)
_UNIT_MULTIPLIERS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def _to_bytes(value: str, unit: str) -> Optional[int]:
    mult = _UNIT_MULTIPLIERS.get(unit)
    if mult is None:
        return None
    return int(float(value) * mult)


def parse_migration_progress(log_lines: list[dict]) -> Optional[dict]:
    """Scans task log entries (newest last, PVE's own order) for the most
    recent 'migration active, transferred X of Y VM-state[, Z/s]' line and
    returns structured progress, or None if no such line is present yet
    (e.g. still in the setup/handshake phase)."""
    for entry in reversed(log_lines):
        line = entry.get("t", "")
        m = _MIGRATE_PROGRESS_RE.search(line)
        if not m:
            continue
        transferred = _to_bytes(m.group(1), m.group(2))
        total = _to_bytes(m.group(3), m.group(4))
        rate = _to_bytes(m.group(5), m.group(6)) if m.group(5) else None
        pct = round(100 * transferred / total, 1) if transferred and total else None
        return {
            "transferred_bytes": transferred,
            "total_bytes": total,
            "rate_bytes_per_sec": rate,
            "pct": pct,
            "raw_line": line,
            "log_line_n": entry.get("n"),
        }
    return None


@dataclass
class TaskResult:
    upid: str
    status: str  # 'running' | 'stopped'
    exit_status: Optional[str]  # None while running; 'OK' or an error string once stopped
    raw: dict

    @property
    def succeeded(self) -> bool:
        return self.status == "stopped" and self.exit_status == "OK"


class PveMaintenanceClient:
    def __init__(self, creds: PveCredentials, timeout: float = 15.0, *, transport=None):
        self._creds = creds
        self._pool = _EndpointPool(creds, timeout, transport)

    @property
    def _client(self) -> httpx.Client:
        return self._pool.client

    def close(self):
        self._pool.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self._pool.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            if "certificate" in str(exc).lower() or "SSL" in str(exc):
                raise PveTlsError(str(exc)) from exc
            raise PveConnectionError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise PveConnectionError(f"timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise PveConnectionError(str(exc)) from exc

        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json().get("data")

    def _get(self, path: str, params: Optional[dict] = None):
        return self._request("GET", path, params=params)

    # -- task monitoring (GET -- safe regardless of the mutations switch) ---

    def task_log(self, node: str, upid: str, start: int = 0, limit: int = 50) -> list[dict]:
        """GET /nodes/{node}/tasks/{upid}/log -- the task's own append-only
        log. Each entry is {n, t}; 'n' is a 1-based line number, so a caller
        can pass start=<last n seen> to fetch only new lines on each poll."""
        return self._get(f"/nodes/{node}/tasks/{upid}/log", params={"start": start, "limit": limit}) or []

    def task_status(self, node: str, upid: str) -> TaskResult:
        raw = self._get(f"/nodes/{node}/tasks/{upid}/status") or {}
        return TaskResult(
            upid=upid,
            status=raw.get("status", "unknown"),
            exit_status=raw.get("exitstatus"),
            raw=raw,
        )

    def wait_for_task(
        self, node: str, upid: str, *, timeout: float = 900.0, poll_interval: float = 3.0
    ) -> TaskResult:
        """Block until the task leaves 'running', or raise TaskTimeoutError.

        Does not itself decide success/failure beyond what PVE's own task
        record says -- callers must still independently read back the
        resulting resource state per the Safety Contract; a task reporting
        exitstatus OK is necessary but not sufficient proof of the intended
        outcome.
        """
        deadline = time.monotonic() + timeout
        while True:
            result = self.task_status(node, upid)
            if result.status != "running":
                return result
            if time.monotonic() > deadline:
                raise TaskTimeoutError(f"task {upid} on {node} did not finish within {timeout}s")
            time.sleep(poll_interval)

    # -- write methods --------------------------------------------------
    # Each one checks the kill switch itself, first line, no exceptions.

    def migrate_vm(
        self,
        node: str,
        vmid: int,
        target_node: str,
        *,
        online: bool = True,
        with_local_disks: bool = False,
        target_storage: Optional[str] = None,
    ) -> str:
        """POST /nodes/{node}/qemu/{vmid}/migrate. Returns the UPID.

        online=True requests a live migration; PVE itself will refuse and
        return an error (not silently fall back to offline) if the workload
        isn't actually eligible -- but eligibility should already have been
        confirmed by the maintenance planner / precondition revalidation
        before this is ever called.

        target_storage, if given, is PVE's 'targetstorage' -- relocates the
        VM's disk(s) onto that storage on the destination as part of the
        migration (this is what actually moves storage; without it, PVE
        leaves every disk exactly where it already is, which is why a VM
        already on shared storage stays on shared storage through an
        ordinary migration). Passing target_storage implies with_local_disks
        so a VM with any local-only disk isn't silently blocked.
        """
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("migrate"))
        params = {"target": target_node, "online": 1 if online else 0}
        if with_local_disks or target_storage:
            params["with-local-disks"] = 1
        if target_storage:
            params["targetstorage"] = target_storage
        return self._request("POST", f"/nodes/{node}/qemu/{vmid}/migrate", data=params)

    def shutdown_vm(self, node: str, vmid: int, *, timeout: int) -> str:
        """POST /nodes/{node}/qemu/{vmid}/status/shutdown. Graceful ACPI
        shutdown ONLY -- forceStop is deliberately never set here. If the
        guest doesn't respond within `timeout` seconds, this task fails/
        times out rather than silently force-killing the guest; escalating
        to a forced stop is Stage W3's SEPARATE, higher-risk
        workload.force_stop operation type, never an automatic fallback."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("shut down"))
        _guard_self_vmid(vmid, "shut down")
        return self._request(
            "POST", f"/nodes/{node}/qemu/{vmid}/status/shutdown",
            data={"timeout": timeout, "forceStop": 0},
        )

    def reboot_vm(self, node: str, vmid: int, *, timeout: int) -> str:
        """POST /nodes/{node}/qemu/{vmid}/status/reboot. Graceful ACPI
        shutdown followed by PVE automatically starting the guest back up --
        a single PVE task, not two separate ones. Same `timeout` semantics
        as shutdown_vm: if the guest doesn't respond to the ACPI signal
        within it, the task fails rather than escalating to a hard reset --
        workload.force_stop is the separate, higher-risk path for that, same
        as it is for a plain shutdown.

        Deliberately NOT guarded by _guard_self_vmid, unlike shutdown_vm/
        force_stop_vm: PVE's own reboot task guarantees the power-back-on
        itself, at the hypervisor level -- it does not
        depend on PyXie's worker process surviving, which is exactly the
        process that gets killed the moment the VM it runs on reboots.
        resume_inflight_operations() already exists precisely to pick an
        operation back up after a worker crash/restart, so a reboot of
        the VM PyXie itself runs on behaves like any other resumable
        operation, not like a shutdown with nothing guaranteed to bring
        it back."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("reboot"))
        return self._request(
            "POST", f"/nodes/{node}/qemu/{vmid}/status/reboot",
            data={"timeout": timeout},
        )

    def start_vm(self, node: str, vmid: int) -> str:
        """POST /nodes/{node}/qemu/{vmid}/status/start."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("start"))
        return self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/start", data={})

    def set_vm_config(self, node: str, vmid: int, *, cores: Optional[int] = None, memory_mb: Optional[int] = None) -> None:
        """PUT /nodes/{node}/qemu/{vmid}/config. Synchronous -- a plain
        cores/memory change applies immediately, no PVE task/UPID involved
        (unlike migrate/shutdown/start). Callers are expected to have
        already shut the guest down first: cores/memory aren't hot-pluggable
        on a normally-configured guest (no 'hotplug' option set), and
        Stage workload.resize enforces that ordering itself rather than
        relying on this method to check."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("reconfigure"))
        params = {}
        if cores is not None:
            params["cores"] = cores
        if memory_mb is not None:
            params["memory"] = memory_mb
        if not params:
            return
        self._request("PUT", f"/nodes/{node}/qemu/{vmid}/config", data=params)

    def set_vm_network_vlan(
        self, node: str, vmid: int, *, guest_type: str, net_id: str, current_net: str, tag: Optional[int],
    ) -> None:
        """PUT /nodes/{node}/qemu|lxc/{vmid}/config -- update one NIC's VLAN
        tag in place, preserving every other parameter on that net device
        (bridge, model/MAC, firewall, rate, mtu, trunks...). Synchronous,
        like set_vm_config -- a NIC config change applies immediately, no
        PVE task/UPID. Hot-pluggable on a running guest (unlike
        cores/memory), which is why workload.network_vlan_change has no
        shutdown/start stages the way workload.resize does.

        `current_net` is the net device string the caller just read live,
        moments before this call -- used only to patch the tag onto it
        while leaving every other param untouched. Not trusted as the sole
        safety check: the workflow's revalidation stage re-reads live
        config before ever calling this, same as every other write here."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("change VLAN"))
        new_net = _set_net_tag(current_net, tag)
        kind = "qemu" if guest_type == "vm" else "lxc"
        self._request("PUT", f"/nodes/{node}/{kind}/{vmid}/config", data={net_id: new_net})

    def update_backup_job(
        self, job_id: str, *, all_guests: bool = False, vmid: Optional[str] = None, exclude: Optional[str] = None,
    ) -> None:
        """PUT /cluster/backup/{id}. Synchronous, like set_vm_config -- a
        vzdump job definition change applies immediately, no PVE task/UPID.

        Three real PVE job shapes, matched to how a human actually toggles
        VMs in the UI:
          - all_guests=True, exclude=None  -> every known guest, no exceptions
          - all_guests=True, exclude=<list> -> every guest EXCEPT these
            (unchecking one VM while already in all-guests mode uses PVE's
            own `exclude` -- future VMs still join automatically, only the
            explicitly-excluded ones don't)
          - all_guests=False, vmid=<list>   -> only these guests

        `delete` clears whichever of vmid/exclude/all doesn't apply to the
        target shape, so switching modes doesn't leave a stale property
        behind for PVE to reconcile ambiguously.
        """
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("update backup job"))
        if all_guests:
            params: dict = {"all": 1}
            to_delete = ["vmid"]
            if exclude:
                params["exclude"] = exclude
            else:
                to_delete.append("exclude")
            params["delete"] = ",".join(to_delete)
        else:
            params = {"all": 0, "vmid": vmid or "", "delete": "exclude"}
        self._request("PUT", f"/cluster/backup/{job_id}", data=params)

    def force_stop_vm(self, node: str, vmid: int) -> str:
        """POST /nodes/{node}/qemu/{vmid}/status/stop -- immediate power-off,
        no ACPI grace period. Higher-risk, separate operation type
        (workload.force_stop) -- never invoked as a timeout fallback from
        shutdown_vm."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("force-stop"))
        _guard_self_vmid(vmid, "force-stop")
        return self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/stop", data={})

    # -- read methods that need Sys.Modify, which the inventory/PVEAuditor
    # credential deliberately does NOT have -- confirmed from PVE's own
    # APAT.pm source that even LISTING available updates requires
    # Sys.Modify (a real PVE API quirk, not a PyXie choice), so these two
    # have to go through the maintenance credential even though they never
    # write anything. -------------------------------------------------

    def list_updates(self, node: str) -> list[dict]:
        return self._get(f"/nodes/{node}/apt/update") or []

    def repositories(self, node: str) -> dict:
        return self._get(f"/nodes/{node}/apt/repositories") or {}

    def reboot_node(self, node: str) -> str:
        """POST /nodes/{node}/status with command=reboot. Stage W5 only --
        every pre-check (quorum, HA, no active backup/migration/package
        operation) must already have passed before this is ever called."""
        if not _mutations_enabled():
            raise MutationsDisabledError(_disabled_message("reboot"))
        return self._request("POST", f"/nodes/{node}/status", data={"command": "reboot"})
