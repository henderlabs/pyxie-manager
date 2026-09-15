"""Structural Veeam Backup & Replication REST API client.

STATUS: Implemented, Contract Tested. NOT live-validated -- no Veeam
environment is available in this lab. Method shapes follow Veeam's
documented v1 REST API (OAuth2 client-credentials handshake, then
Bearer-token GETs). The OAuth2 token exchange is a POST to Veeam's own
auth endpoint -- that is authentication plumbing against Veeam, not a
write operation against PVE, and does not weaken the PVE safety boundary.
This class has no method that creates, modifies, or deletes a Veeam job,
backup, or restore -- read/query only, mirroring the PBS/PVE clients.
"""

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .pve_client import PveAuthError, PveConnectionError, PveTlsError


@dataclass
class VeeamCredentials:
    hostname: str
    api_port: int
    username: str
    password: str
    tls_verify: bool = True


class VeeamClient:
    def __init__(self, creds: VeeamCredentials, timeout: float = 10.0):
        self._creds = creds
        self._base_url = f"https://{creds.hostname}:{creds.api_port}/api/v1"
        self._client = httpx.Client(base_url=self._base_url, verify=creds.tls_verify, timeout=timeout)
        self._token: Optional[str] = None

    def close(self):
        self._client.close()

    def __enter__(self):
        self._authenticate()
        return self

    def __exit__(self, *exc):
        self.close()

    def _authenticate(self):
        # Veeam v1 API: OAuth2 client-credentials/password grant against the
        # server's own token endpoint -- not a PVE call, and not a Veeam
        # mutation (it establishes a session, same role as a PVE API token).
        try:
            resp = self._client.post(
                "/token",
                data={"grant_type": "password", "username": self._creds.username, "password": self._creds.password},
                headers={"x-api-version": "1.1-rev1"},
            )
        except httpx.ConnectError as exc:
            if "certificate" in str(exc).lower():
                raise PveTlsError(str(exc)) from exc
            raise PveConnectionError(str(exc)) from exc
        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        self._token = resp.json().get("access_token")
        self._client.headers["Authorization"] = f"Bearer {self._token}"

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._client.get(path, params=params, headers={"x-api-version": "1.1-rev1"})
        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json()

    def jobs(self) -> list[dict]:
        return self._get("/jobs").get("data", [])

    def job_states(self) -> list[dict]:
        return self._get("/jobs/states").get("data", [])

    def backup_objects(self) -> list[dict]:
        return self._get("/backupObjects").get("data", [])

    def sessions(self, limit: int = 50) -> list[dict]:
        return self._get("/sessions", params={"limit": limit}).get("data", [])


def normalize_veeam_protection(vm_name: str, job_states: list[dict], sessions: list[dict]) -> dict:
    """Maps Veeam's job/session shape to our generic protection_results
    columns. Pure function so it can be contract-tested without a live
    Veeam server -- see tests/test_veeam_normalize.py.
    """
    relevant = [j for j in job_states if vm_name in (j.get("lastResult", {}).get("objectsCount", {}) or {})]
    if not relevant:
        return {"protected": "unknown", "last_successful_job_at": None, "confidence": "insufficient_data"}

    job = relevant[0]
    last_run = job.get("lastRun")
    last_result = (job.get("lastResult") or {}).get("result")
    return {
        "protected": "true" if last_result == "Success" else "false",
        "last_successful_job_at": last_run,
        "confidence": "moderate",
    }
