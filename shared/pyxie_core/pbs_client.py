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

    def snapshots(self, store: str, backup_type: str, backup_id: str) -> list[dict]:
        return self._get(
            f"/admin/datastore/{store}/snapshots",
            params={"backup-type": backup_type, "backup-id": backup_id},
        )
