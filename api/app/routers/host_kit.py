"""Single-file host installer: admin creates a short-lived link, a PVE node downloads it (no login) and runs it.

The link carries no secret: the installer holds scripts plus the PUBLIC key. Its integrity comes from the SHA-256 the
admin sees in PyXie (an authenticated channel) and checks on the host, not from trusting the network path.
"""

import os
import uuid

import redis
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from pyxie_core import host_kit as hk
from pyxie_core.audit import write_audit_event
from pyxie_core.crypto import decrypt_secret
from pyxie_core.host_maintenance_client import derive_public_key_line
from pyxie_core.models import HostMaintenanceCredential, PveTarget

from .. import config
from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

KIT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "host_maintenance_kit")

admin_router = APIRouter(tags=["host-kit"], dependencies=[Depends(get_current_user)])
public_router = APIRouter(tags=["host-kit-public"])

_redis = None


def _r():
    global _redis
    if _redis is None:
        _redis = redis.from_url(os.environ["REDIS_URL"])
    return _redis


def _build(db: Session, target_id) -> tuple[bytes, PveTarget]:
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "PVE target not found")
    cred = db.query(HostMaintenanceCredential).filter(HostMaintenanceCredential.pve_target_id == target_id).one_or_none()
    if cred is None:
        raise HTTPException(409, "no host-maintenance credential generated for this target yet -- generate one first")
    pub = derive_public_key_line(decrypt_secret(cred.encrypted_private_key))
    return hk.build_installer(KIT_DIR, pub, target.name, config.settings.APP_VERSION), target


@admin_router.post("/api/pve-targets/{target_id}/host-kit/link", dependencies=[Depends(require_admin)])
def create_link(target_id: uuid.UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    data, target = _build(db, target_id)
    token = hk.new_link_token()
    hk.store_link(_r(), token, str(target_id))
    write_audit_event(
        db, event_category="credential", event_type="host_kit.link_created", actor=user.email, actor_type="user",
        provider_id=target.provider_id,
        metadata={"pve_target_id": str(target_id), "kit_version": config.settings.APP_VERSION, "sha256": hk.sha256_hex(data)},
    )
    return {
        "path": f"/host-kit/{token}", "sha256": hk.sha256_hex(data), "size": len(data),
        "expires_in": hk.LINK_TTL_SECONDS, "kit_version": config.settings.APP_VERSION,
        "wrapper_version": hk.wrapper_version(KIT_DIR),
    }


@admin_router.get("/api/pve-targets/{target_id}/host-kit.sh", dependencies=[Depends(require_admin)])
def download_installer(target_id: uuid.UUID, db: Session = Depends(get_db)):
    data, _ = _build(db, target_id)
    return Response(data, media_type="text/x-shellscript",
                    headers={"Content-Disposition": "attachment; filename=pyxie-host-kit.sh"})


@public_router.get("/api/host-kit/{token}")
def public_download(token: str, db: Session = Depends(get_db)):
    target_id = hk.resolve_link(_r(), token)
    if target_id is None:
        raise HTTPException(404, "link expired or not found")
    data, target = _build(db, uuid.UUID(target_id))
    write_audit_event(
        db, event_category="credential", event_type="host_kit.downloaded", actor="host", actor_type="system",
        provider_id=target.provider_id, metadata={"pve_target_id": target_id, "sha256": hk.sha256_hex(data)},
    )
    return Response(data, media_type="text/plain", headers={"Content-Disposition": "attachment; filename=pyxie-host-kit.sh", "Cache-Control": "no-store"})
