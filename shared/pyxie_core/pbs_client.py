"""Read-only Proxmox Backup Server API client. GET only, same safety
pattern as pve_client.py -- no write method exists on this class.
"""

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .pve_client import PveAuthError, PveConnectionError, PveTlsError


@dataclass
class PbsCredentials:
    hostname: str
    api_port: int
    token_user: str
    token_id: str
    token_secret: str
    tls_verify: bool = True


class PbsClient:
    def __init__(self, creds: PbsCredentials, timeout: float = 10.0):
        self._creds = creds
        base_url = f"https://{creds.hostname}:{creds.api_port}/api2/json"
        self._client = httpx.Client(
            base_url=base_url,
            verify=creds.tls_verify,
            timeout=timeout,
            headers={
                # PBS uses ':' before the secret (PVE uses '=') -- easy to
                # get wrong since the two products otherwise look identical.
                "Authorization": f"PBSAPIToken={creds.token_user}!{creds.token_id}:{creds.token_secret}"
            },
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        try:
            resp = self._client.get(path, params=params)
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

    def version(self) -> dict:
        return self._get("/version")

    def datastores(self) -> list[dict]:
        return self._get("/admin/datastore")

    def groups(self, store: str) -> list[dict]:
        return self._get(f"/admin/datastore/{store}/groups")

    # --- server status and jobs (all GET) ---
    def node_status(self) -> dict:
        return self._get("/nodes/localhost/status")

    def tasks(self, *, running: bool = False, errors: bool = False, limit: int = 50, since: Optional[int] = None) -> list[dict]:
        params: dict = {"limit": limit}
        if running:
            params["running"] = 1
        if errors:
            params["errors"] = 1
        if since:
            params["since"] = since
        return self._get("/nodes/localhost/tasks", params=params) or []

    def apt_updates(self) -> list[dict]:
        return self._get("/nodes/localhost/apt/update") or []

    def apt_versions(self) -> list[dict]:
        return self._get("/nodes/localhost/apt/versions") or []

    def subscription(self) -> dict:
        return self._get("/nodes/localhost/subscription") or {}

    def datastore_usage(self) -> list[dict]:
        return self._get("/status/datastore-usage") or []

    def remotes(self) -> list[dict]:
        return self._get("/config/remote") or []

    def job_config(self, kind: str) -> list[dict]:
        """kind: sync | prune | verify. Configuration (what is set); job_state() has the last and next run."""
        return self._get(f"/config/{_job_kind(kind)}") or []

    def job_state(self, kind: str) -> list[dict]:
        return self._get(f"/admin/{_job_kind(kind)}") or []

    def snapshots(self, store: str, backup_type: str, backup_id: str, ns: str = "") -> list[dict]:
        params = {"backup-type": backup_type, "backup-id": backup_id}
        if ns:
            params["ns"] = ns
        return self._get(f"/admin/datastore/{store}/snapshots", params=params)

    def namespaces(self, store: str) -> list[str]:
        """The namespaces of a datastore ('' is the datastore itself); just the root when the server cannot list them."""
        try:
            return [x.get("ns", "") for x in (self._get(f"/admin/datastore/{store}/namespace") or [])] or [""]
        except Exception:  # noqa: BLE001
            return [""]


JOB_KINDS = ("sync", "prune", "verify")


def _job_kind(kind: str) -> str:
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown PBS job kind {kind!r}")
    return kind


class PbsWriteClient(PbsClient):
    """The only PBS class that can change anything, and only sync, prune and verify jobs (create, update, delete,
    run now). Same global write switch as every other PyXie mutation. The caller (pbs_job_workflow) does the
    preview, approval, revalidation, locking, read-back and audit."""

    def _send(self, method: str, path: str, data: Optional[dict] = None) -> Any:
        from .host_maintenance_client import MutationsDisabledError, _mutations_enabled
        if not _mutations_enabled():
            raise MutationsDisabledError("PBS writes are disabled (Platform > Settings, PVE writes switch)")
        try:
            resp = self._client.request(method, path, data=data)
        except httpx.ConnectError as exc:
            raise PveConnectionError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise PveConnectionError(str(exc)) from exc
        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise PbsApiError(resp.status_code, resp.text[:300])
        return resp.json().get("data")

    def create_job(self, kind: str, params: dict) -> Any:
        return self._send("POST", f"/config/{_job_kind(kind)}", params)

    def update_job(self, kind: str, job_id: str, params: dict, delete: Optional[list] = None) -> Any:
        body = dict(params)
        if delete:
            body["delete"] = list(delete)
        return self._send("PUT", f"/config/{_job_kind(kind)}/{job_id}", body)

    def delete_job(self, kind: str, job_id: str) -> Any:
        return self._send("DELETE", f"/config/{_job_kind(kind)}/{job_id}")

    def set_datastore(self, store: str, params: dict, delete: Optional[list] = None) -> Any:
        """PUT /config/datastore/{store}: only what PyXie edits (the garbage collection schedule)."""
        body = dict(params)
        if delete:
            body["delete"] = list(delete)
        return self._send("PUT", f"/config/datastore/{store}", body)

    def gc_run(self, store: str) -> Any:
        """POST /admin/datastore/{store}/gc: starts garbage collection; answers with the task id."""
        return self._send("POST", f"/admin/datastore/{store}/gc")

    def verify_run(self, store: str, params: Optional[dict] = None) -> Any:
        """POST /admin/datastore/{store}/verify: verify one backup, one guest's backups, or the whole datastore; answers with the task id."""
        return self._send("POST", f"/admin/datastore/{store}/verify", dict(params or {}))

    def set_protected(self, store: str, params: dict) -> Any:
        """PUT /admin/datastore/{store}/protected: protect or unprotect one backup."""
        return self._send("PUT", f"/admin/datastore/{store}/protected", dict(params))

    def reboot(self) -> Any:
        """Restart the server (the same call Proxmox VE uses for a node). Needs Sys.PowerManagement."""
        return self._send("POST", "/nodes/localhost/status", {"command": "reboot"})

    def run_job(self, kind: str, job_id: str) -> Any:
        """Starts the job now; PBS answers with the task id (UPID)."""
        return self._send("POST", f"/admin/{_job_kind(kind)}/{job_id}/run")


class PbsApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"PBS answered {status}: {detail}")
        self.status = status
        self.detail = detail
