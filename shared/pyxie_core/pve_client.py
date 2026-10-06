"""Read-only Proxmox VE API client.

This client exposes GET only. There is no post/put/delete method anywhere in
this class, by design -- Phase 0's read-only guardrail is enforced by this
client having no executable path to mutate PVE, not merely by hiding buttons
in the UI. Do not add write methods here without an explicit Phase 1 approval.
"""

import threading
import time
from dataclasses import dataclass, field
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
    # Other cluster members to try, in order, when the primary hostname cannot
    # be connected to at all (see _EndpointPool). Any member serves the whole
    # cluster API, so these are interchangeable with `hostname`.
    fallback_hostnames: list = field(default_factory=list)


# Failover tuning. A node that refuses/blackholes connections is remembered as
# "bad" for BAD_ENDPOINT_TTL seconds (per process) so that the next clients do
# not each re-pay a connect timeout against it; after the TTL it is tried first
# again, so a recovered primary is picked up automatically.
CONNECT_TIMEOUT = 5.0
BAD_ENDPOINT_TTL = 60.0
_bad_endpoints: dict = {}
_bad_lock = threading.Lock()


def mark_endpoint_bad(host: str) -> None:
    with _bad_lock:
        _bad_endpoints[host] = time.monotonic() + BAD_ENDPOINT_TTL


def is_endpoint_bad(host: str) -> bool:
    with _bad_lock:
        expiry = _bad_endpoints.get(host)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            del _bad_endpoints[host]
            return False
        return True


def clear_bad_endpoints() -> None:
    with _bad_lock:
        _bad_endpoints.clear()


class _EndpointPool:
    """One httpx client at a time, moving to the next cluster member when the
    current one cannot be CONNECTED to (refused, DNS failure, connect timeout).

    Deliberately NOT failed over: auth errors (401/403 -- every node would say
    the same), TLS certificate errors (a misconfiguration, not an outage),
    read timeouts (the node is up but slow -- the callers retry those), and
    HTTP error statuses. A connect failure means the request never reached the
    server, so repeating it on another node is safe even for writes."""

    def __init__(self, creds: "PveCredentials", timeout: float, transport=None):
        seen: list = []
        for h in [creds.hostname, *(creds.fallback_hostnames or [])]:
            if h and h not in seen:
                seen.append(h)
        self.preferred_host = seen[0]
        self.skipped_bad = [h for h in seen if is_endpoint_bad(h)]
        self.hosts = [h for h in seen if h not in self.skipped_bad] + self.skipped_bad
        self.failed: list = []
        self._creds = creds
        self._timeout = timeout
        self._transport = transport
        self._lock = threading.Lock()
        self._retired: list = []
        self._idx = 0
        self.client = self._make_client(self.hosts[0])

    def _make_client(self, host: str) -> httpx.Client:
        creds = self._creds
        return httpx.Client(
            base_url=f"https://{host}:{creds.api_port}/api2/json",
            verify=creds.tls_verify,
            timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, CONNECT_TIMEOUT)),
            transport=self._transport,
            headers={
                "Authorization": (
                    f"PVEAPIToken={creds.token_user}!{creds.token_id}="
                    f"{creds.token_secret}"
                )
            },
        )

    @property
    def active_host(self) -> str:
        return self.hosts[self._idx]

    @property
    def unhealthy(self) -> list:
        """Endpoints that failed during this client's life, plus those it
        started out avoiding because they had failed moments earlier."""
        out: list = []
        for h in [f["host"] for f in self.failed] + self.skipped_bad:
            if h not in out:
                out.append(h)
        return out

    def _failover(self, failed_host: str, exc: Exception) -> bool:
        with self._lock:
            if self.active_host != failed_host:
                return True  # another thread already moved on; just retry
            self.failed.append({"host": failed_host, "error": str(exc)[:200]})
            mark_endpoint_bad(failed_host)
            if self._idx + 1 >= len(self.hosts):
                return False
            self._idx += 1
            self._retired.append(self.client)  # closed in close(); other threads may still hold it
            self.client = self._make_client(self.active_host)
            return True

    def request(self, method: str, path: str, **kwargs):
        while True:
            with self._lock:
                client, host = self.client, self.active_host
            try:
                return client.request(method, path, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if isinstance(exc, httpx.ConnectError) and ("certificate" in str(exc).lower() or "SSL" in str(exc)):
                    raise PveTlsError(str(exc)) from exc
                if self._failover(host, exc):
                    continue
                if len(self.hosts) == 1:
                    if isinstance(exc, httpx.ConnectTimeout):
                        raise  # single endpoint: the caller's own timeout retry applies
                    raise PveConnectionError(str(exc)) from exc
                detail = "; ".join(f"{f['host']}: {f['error']}" for f in self.failed)
                raise PveConnectionError(f"all {len(self.hosts)} PVE endpoints unreachable -- {detail}") from exc

    def close(self) -> None:
        for c in [*self._retired, self.client]:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


class PveClient:
    # 10s was too tight for a genuinely heavy node -- pve-slc-m401 (nearly
    # 2x any other node's VM count, ~80% RAM under real load) consistently
    # takes ~14.5s for its own /qemu listing specifically, measured 5/5 real
    # requests. That's real, reproducible latency from a large response on a
    # loaded host, not a hang -- a timeout retry can't fix a call that's
    # reliably slower than the timeout itself, only a longer timeout can.
    def __init__(self, creds: PveCredentials, timeout: float = 25.0, *, transport=None):
        self._creds = creds
        self._pool = _EndpointPool(creds, timeout, transport)

    @property
    def _client(self) -> httpx.Client:
        return self._pool.client

    @property
    def active_host(self) -> str:
        return self._pool.active_host

    @property
    def preferred_host(self) -> str:
        return self._pool.preferred_host

    @property
    def unhealthy_endpoints(self) -> list:
        return self._pool.unhealthy

    def close(self):
        self._pool.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, params: Optional[dict] = None, *, retries: int = 1, timeout: Optional[float] = None) -> Any:
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
                resp = self._pool.request("GET", path, params=params, **({"timeout": timeout} if timeout else {}))
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

    def cluster_log(self, max_entries: int = 500) -> list[dict]:
        """GET /cluster/log -- PVE's own syslog-style rolling log, merged
        across every node in the cluster (daemon restarts, corosync/quorum
        events, hardware/storage issues logged at the OS level, etc.).
        Distinct from /cluster/tasks (a job's outcome) and /nodes/{node}/
        tasks (this class's tasks() method) -- this is ambient system
        activity, not a PyXie- or user-triggered job. PVE itself only
        keeps a small rolling buffer (its own default page size), so a
        caller wanting real history needs to poll and persist this
        periodically, not treat it as a queryable archive on PVE's side."""
        return self._get("/cluster/log", params={"max": max_entries}) or []

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

    def qemu_status_current(self, node: str, vmid: int) -> dict:
        """Live per-VM status, including `ballooninfo` (guest free/total memory,
        cumulative swap-in/out and page-fault counters) when the guest reports it."""
        return self._get(f"/nodes/{node}/qemu/{vmid}/status/current")

    def lxc_status_current(self, node: str, vmid: int) -> dict:
        return self._get(f"/nodes/{node}/lxc/{vmid}/status/current")

    def qemu_snapshots(self, node: str, vmid: int) -> list[dict]:
        return self._get(f"/nodes/{node}/qemu/{vmid}/snapshot") or []

    def lxc_snapshots(self, node: str, vmid: int) -> list[dict]:
        return self._get(f"/nodes/{node}/lxc/{vmid}/snapshot") or []

    def qemu_agent_interfaces(self, node: str, vmid: int) -> list[dict]:
        """Guest-agent network interfaces -- only answers when the agent is
        running inside the guest, so it gets a short timeout and no retry:
        a guest without a responsive agent must not stall the whole pass."""
        data = self._get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", retries=0, timeout=6.0)
        return (data or {}).get("result") or []

    def lxc_interfaces(self, node: str, vmid: int) -> list[dict]:
        return self._get(f"/nodes/{node}/lxc/{vmid}/interfaces", retries=0, timeout=6.0) or []

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
