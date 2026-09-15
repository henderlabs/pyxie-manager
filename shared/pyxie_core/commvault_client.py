"""Structural Commvault REST API client.

STATUS: Implemented, Contract Tested. NOT live-validated -- do not connect
this to a real Commvault environment without deliberately validating it
first; no live credentials are configured for this by default. Method
shapes follow Commvault's documented REST API (token-based auth via
/Login, then Authtoken-header GETs). Read/query only -- no method creates,
modifies, or deletes a Commvault job, schedule, or restore.
"""

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .pve_client import PveAuthError, PveConnectionError, PveTlsError


@dataclass
class CommvaultCredentials:
    hostname: str
    api_port: int
    username: str
    password_token: str  # Commvault expects a base64/triple-DES token, not a raw password
    tls_verify: bool = True


class CommvaultClient:
    def __init__(self, creds: CommvaultCredentials, timeout: float = 10.0):
        self._creds = creds
        self._base_url = f"https://{creds.hostname}:{creds.api_port}/webconsole/api"
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
        try:
            resp = self._client.post(
                "/Login",
                json={"username": self._creds.username, "password": self._creds.password_token},
            )
        except httpx.ConnectError as exc:
            if "certificate" in str(exc).lower():
                raise PveTlsError(str(exc)) from exc
            raise PveConnectionError(str(exc)) from exc
        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        self._token = resp.json().get("token")
        self._client.headers["Authtoken"] = self._token or ""

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._client.get(path, params=params)
        if resp.status_code in (401, 403):
            raise PveAuthError(f"{resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json()

    def client_list(self) -> list[dict]:
        return self._get("/Client").get("clientProperties", [])

    def job_list(self, client_id: Optional[str] = None) -> list[dict]:
        params = {"clientId": client_id} if client_id else None
        return self._get("/Job", params=params).get("jobs", [])

    def schedule_list(self) -> list[dict]:
        return self._get("/Schedules").get("taskDetail", [])


def normalize_commvault_protection(vm_client_name: str, jobs: list[dict]) -> dict:
    """Pure normalization function -- contract-tested without a live
    Commvault server, see tests/test_commvault_normalize.py."""
    relevant = [j for j in jobs if j.get("subclient", {}).get("clientName") == vm_client_name]
    if not relevant:
        return {"protected": "unknown", "last_successful_job_at": None, "confidence": "insufficient_data"}

    successes = [j for j in relevant if j.get("status") == "Completed"]
    if not successes:
        return {"protected": "false", "last_successful_job_at": None, "confidence": "moderate"}

    latest = max(successes, key=lambda j: j.get("jobEndTime", 0))
    return {
        "protected": "true",
        "last_successful_job_at": latest.get("jobEndTime"),
        "confidence": "moderate",
    }
