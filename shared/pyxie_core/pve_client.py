"""Read-only Proxmox VE API client.

This client exposes GET only. There is no post/put/delete method anywhere in
this class, by design -- Phase 0's read-only guardrail is enforced by this
client having no executable path to mutate PVE, not merely by hiding buttons
in the UI. Do not add write methods here without an explicit Phase 1 approval.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


class PveConnectionError(Exception):
    """Raised for network-level failures (host unreachable, DNS, timeout)."""


class PveTlsError(Exception):
    """Raised when TLS certificate validation fails."""


class PveAuthError(Exception):
    """Raised for authentication/authorization failures (401/403)."""


@dataclass
class PveCredentials:
    hostname: str
    api_port: int
    token_user: str  # e.g. pyxie-manager@pve
    token_id: str  # e.g. inventory
    token_secret: str
    tls_verify: bool = True


class PveClient:
    # 10s was too tight for a genuinely heavy node -- pve-slc-m401 (nearly
    # 2x any other node's VM count, ~80% RAM under real load) consistently
    # takes ~14.5s for its own /qemu listing specifically, measured 5/5 real
    # requests. That's real, reproducible latency from a large response on a
    # loaded host, not a hang -- a timeout retry can't fix a call that's
    # reliably slower than the timeout itself, only a longer timeout can.
    def __init__(self, creds: PveCredentials, timeout: float = 25.0):
        self._creds = creds
        base_url = f"https://{creds.hostname}:{creds.api_port}/api2/json"
        self._client = httpx.Client(
            base_url=base_url,
            verify=creds.tls_verify,
            timeout=timeout,
            headers={
                "Authorization": (
                    f"PVEAPIToken={creds.token_user}!{creds.token_id}="
                    f"{creds.token_secret}"
                )
            },
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, params: Optional[dict] = None, *, retries: int = 1) -> Any:
        """retries=1 means one extra attempt after the first timeout, not one
        attempt total -- found live against a real node whose /qemu listing
        times out intermittently (roughly half the time) but responds in
        under a second when it does work, consistent with the PVE API
        worker being transiently busy rather than genuinely broken. Only
        retried for httpx.TimeoutException specifically -- a connect/TLS/
        auth failure won't resolve itself on a second attempt a moment
        later, so those still raise immediately."""
        attempt = 0
        while True:
            try:
                resp = self._client.get(path, params=params)
            except httpx.ConnectError as exc:
                if "certificate" in str(exc).lower() or "SSL" in str(exc):
                    raise PveTlsError(str(exc)) from exc
                raise PveConnectionError(str(exc)) from exc
            except httpx.TimeoutException as exc:
                if attempt < retries:
                    attempt += 1
                    time.sleep(1)
                    continue
                raise PveConnectionError(f"timeout: {exc}") from exc
            except httpx.TransportError as exc:
                raise PveConnectionError(str(exc)) from exc
            else:
                break

        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json().get("data")

    # -- read-only endpoints actually used by Phase 0 -----------------------

    def version(self) -> dict:
        return self._get("/version")

    def cluster_status(self) -> list[dict]:
        return self._get("/cluster/status")

    def cluster_resources(self, type_: Optional[str] = None) -> list[dict]:
        params = {"type": type_} if type_ else None
        return self._get("/cluster/resources", params=params)

    def nodes(self) -> list[dict]:
        return self._get("/nodes")

    def node_status(self, node: str) -> dict:
        return self._get(f"/nodes/{node}/status")

    def node_version(self, node: str) -> dict:
        return self._get(f"/nodes/{node}/version")

    def node_apt_update_count(self, node: str) -> int:
        try:
            updates = self._get(f"/nodes/{node}/apt/update")
            return len(updates or [])
        except Exception:
            return None

    def node_apt_updates(self, node: str) -> list[dict]:
        """Full pending-package detail (Package/OldVersion/Version/Priority/
        Section/...), not just the count -- same endpoint as
        node_apt_update_count(), which discards everything but the length.
        Unlike that method, this does not swallow exceptions: a caller
        showing a real package list to a user needs to know when it failed,
        not silently see an empty list and assume nothing is pending."""
        return self._get(f"/nodes/{node}/apt/update") or []

    def qemu_list(self, node: str) -> list[dict]:
        return self._get(f"/nodes/{node}/qemu")

    def qemu_config(self, node: str, vmid: int) -> dict:
        return self._get(f"/nodes/{node}/qemu/{vmid}/config")

    def lxc_list(self, node: str) -> list[dict]:
        return self._get(f"/nodes/{node}/lxc")

    def lxc_config(self, node: str, vmid: int) -> dict:
        return self._get(f"/nodes/{node}/lxc/{vmid}/config")

    def storage_list(self, node: str) -> list[dict]:
        return self._get(f"/nodes/{node}/storage")

    def node_network(self, node: str) -> list[dict]:
        """GET /nodes/{node}/network -- host-level interfaces (bridges,
        bonds, vlans, physical NICs). Read-only: PyXie has no write path for
        this (a bad host network apply can require console access to fix,
        unlike every other PVE write this app makes). Used to show topology
        and to know which bridges are VLAN-aware / which VLAN IDs are
        actually allowed, so a workload's NIC can be validated against real
        network config rather than accepting any tag 1-4094."""
        return self._get(f"/nodes/{node}/network")

    def tasks(self, node: str, limit: int = 20, *, source: str | None = None, vmid: Optional[int] = None) -> list[dict]:
        """GET /nodes/{node}/tasks. `source` defaults to PVE's own default
        ('archive' -- finished tasks only) when omitted, matching existing
        callers (discovery.py wants the historical feed). Pass
        source='active' to see in-progress tasks instead -- 'archive'
        rows never carry status='running'; PVE only puts a task there once
        it's finished, which made a client-side status=='running' filter
        against the default archive list a check that could never match
        anything.
        `vmid` filters server-side (exact match on PVE's own `id` field),
        avoiding a client-side substring check that would false-positive
        on e.g. vmid 100 appearing inside a task for vmid 1100."""
        params: dict = {"limit": limit}
        if source is not None:
            params["source"] = source
        if vmid is not None:
            params["vmid"] = vmid
        return self._get(f"/nodes/{node}/tasks", params=params)

    def node_rrddata(self, node: str, timeframe: str = "hour") -> list[dict]:
        return self._get(f"/nodes/{node}/rrddata", params={"timeframe": timeframe, "cf": "AVERAGE"}) or []

    def qemu_rrddata(self, node: str, vmid: int, timeframe: str = "hour") -> list[dict]:
        return self._get(f"/nodes/{node}/qemu/{vmid}/rrddata", params={"timeframe": timeframe, "cf": "AVERAGE"}) or []

    def lxc_rrddata(self, node: str, vmid: int, timeframe: str = "hour") -> list[dict]:
        return self._get(f"/nodes/{node}/lxc/{vmid}/rrddata", params={"timeframe": timeframe, "cf": "AVERAGE"}) or []

    def backup_jobs(self) -> list[dict]:
        """GET /cluster/backup -- vzdump job definitions (schedule, target
        storage, and either an explicit `vmid` list or `all`/`exclude` for
        all-guests mode). Cluster-wide, not per-node."""
        return self._get("/cluster/backup") or []

    def ha_status(self) -> list[dict]:
        try:
            return self._get("/cluster/ha/status/current")
        except Exception:
            return []

    def ha_groups(self) -> list[dict]:
        """Legacy HA groups (node restriction/preference). Present on all
        supported PVE versions; superseded by /cluster/ha/rules on 8.3+ but
        not removed."""
        try:
            return self._get("/cluster/ha/groups") or []
        except Exception:
            return []

    def ha_resources(self) -> list[dict]:
        """Maps sid (e.g. 'vm:101') -> HA group/state. Used to find which HA
        group, if any, a given VM belongs to."""
        try:
            return self._get("/cluster/ha/resources") or []
        except Exception:
            return []

    def ha_rules(self) -> list[dict]:
        """PVE 8.3+ HA resource affinity/anti-affinity rules. Returns [] on
        older PVE where this endpoint doesn't exist -- callers must treat an
        empty result as 'no rules configured OR not supported', never as a
        confirmed absence of rules, and should fall back to ha_groups()."""
        try:
            return self._get("/cluster/ha/rules") or []
        except Exception:
            return []
