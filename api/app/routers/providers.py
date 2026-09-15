import io
import tarfile
import uuid
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.crypto import decrypt_secret, encrypt_secret, mask_secret
from pyxie_core.discovery import run_discovery
from pyxie_core.host_maintenance_client import derive_public_key_line
from pyxie_core.models import (
    Capability,
    CapabilityGrant,
    HostMaintenanceCredential,
    Provider,
    ProviderCapability,
    PveCredential,
    PveTarget,
)

# Resolves to <api>/host_maintenance_kit regardless of where <api> actually
# lives on disk (Docker's /app or a native install's own path) -- this file
# is always at <api>/app/routers/providers.py, so parents[2] is <api>.
HOST_MAINTENANCE_KIT_DIR = str(Path(__file__).resolve().parents[2] / "host_maintenance_kit")
HOST_MAINTENANCE_KIT_FILES = ("install.sh", "uninstall.sh", "pyxie-maint", "pyxie-maint-ssh-dispatch", "pyxie-maint.sudoers")

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db
from .. import schemas

router = APIRouter(prefix="/api", tags=["providers"], dependencies=[Depends(get_current_user)])


@router.get("/providers", response_model=list[schemas.ProviderOut])
def list_providers(db: Session = Depends(get_db)):
    providers = db.query(Provider).order_by(Provider.name).all()
    out = []
    for p in providers:
        caps = (
            db.query(Capability.id)
            .join(ProviderCapability, ProviderCapability.capability_id == Capability.id)
            .filter(ProviderCapability.provider_id == p.id)
            .all()
        )
        item = schemas.ProviderOut.model_validate(p)
        item.capabilities = [c[0] for c in caps]
        out.append(item)
    return out


@router.get("/pve-targets", response_model=list[schemas.PveTargetOut])
def list_pve_targets(db: Session = Depends(get_db)):
    return db.query(PveTarget).order_by(PveTarget.name).all()


@router.post("/pve-targets", response_model=schemas.PveTargetOut, dependencies=[Depends(require_admin)])
def create_pve_target(payload: schemas.PveTargetCreate, db: Session = Depends(get_db)):
    provider = (
        db.query(Provider)
        .filter(Provider.category_id == "pve", Provider.instance_name == payload.name)
        .one_or_none()
    )
    if provider is None:
        provider = Provider(
            category_id="pve",
            provider_type="pve",
            name="Proxmox VE",
            instance_name=payload.name,
            enabled=True,
            contract_version=1,
            connection_health="unknown",
        )
        db.add(provider)
        db.flush()
        for cap_id in ("pve.inventory.read", "pve.health.read", "pve.tasks.read"):
            db.add(ProviderCapability(provider_id=provider.id, capability_id=cap_id))
            db.add(
                CapabilityGrant(
                    provider_id=provider.id,
                    capability_id=cap_id,
                    scope_type="site",
                    scope_id=payload.site_id,
                    granted_by="system",
                )
            )

    target = PveTarget(
        site_id=payload.site_id,
        provider_id=provider.id,
        name=payload.name,
        hostname=payload.hostname,
        api_port=payload.api_port,
        tls_verify=payload.tls_verify,
    )
    db.add(target)
    db.flush()

    cred = PveCredential(
        pve_target_id=target.id,
        slot_name="inventory",
        token_user=payload.token_user,
        token_id=payload.token_id,
        encrypted_secret=encrypt_secret(payload.token_secret),
        status="untested",
        created_by="user",
    )
    db.add(cred)

    write_audit_event(
        db,
        event_category="provider",
        event_type="pve_target.created",
        actor="user",
        actor_type="user",
        site_id=payload.site_id,
        provider_id=provider.id,
        state_after={"name": payload.name, "hostname": payload.hostname, "api_port": payload.api_port},
    )
    write_audit_event(
        db,
        event_category="credential",
        event_type="credential.created",
        actor="user",
        actor_type="user",
        site_id=payload.site_id,
        provider_id=provider.id,
        metadata={"slot_name": "inventory", "token_user": payload.token_user},
    )
    db.commit()
    db.refresh(target)
    return target


@router.get("/pve-targets/{target_id}/credentials", response_model=list[schemas.CredentialOut])
def list_credentials(target_id: uuid.UUID, db: Session = Depends(get_db)):
    creds = db.query(PveCredential).filter(PveCredential.pve_target_id == target_id).all()
    out = []
    for c in creds:
        item = schemas.CredentialOut.model_validate(c)
        item.masked_secret = mask_secret(decrypt_secret(c.encrypted_secret))
        out.append(item)
    return out


CREDENTIAL_SLOT_NAMES = {"inventory", "maintenance", "administrative"}


@router.post("/pve-targets/{target_id}/credentials", response_model=schemas.CredentialOut, dependencies=[Depends(require_admin)])
def create_credential(
    target_id: uuid.UUID, payload: schemas.CredentialCreate,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Add a named PVE API credential slot (inventory/maintenance/administrative)
    to an existing target. `inventory` is normally created alongside the target
    itself by create_pve_target -- this closes the gap for `maintenance` and
    `administrative`, which previously had no API path at all and had to be
    inserted directly into Postgres by hand before any write-capable feature
    (migration, maintenance, rightsizing apply, etc.) would work."""
    if payload.slot_name not in CREDENTIAL_SLOT_NAMES:
        raise HTTPException(400, f"slot_name must be one of {sorted(CREDENTIAL_SLOT_NAMES)}")

    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "PVE target not found")

    existing = (
        db.query(PveCredential)
        .filter(PveCredential.pve_target_id == target_id, PveCredential.slot_name == payload.slot_name)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(400, f"a '{payload.slot_name}' credential already exists for this target")

    cred = PveCredential(
        pve_target_id=target_id,
        slot_name=payload.slot_name,
        token_user=payload.token_user,
        token_id=payload.token_id,
        encrypted_secret=encrypt_secret(payload.token_secret),
        status="untested",
        created_by=user.email,
    )
    db.add(cred)
    write_audit_event(
        db,
        event_category="credential",
        event_type="credential.created",
        actor=user.email, actor_type="user",
        provider_id=target.provider_id,
        metadata={"pve_target_id": str(target_id), "slot_name": payload.slot_name, "token_user": payload.token_user},
    )
    db.commit()
    db.refresh(cred)
    item = schemas.CredentialOut.model_validate(cred)
    item.masked_secret = mask_secret(payload.token_secret)
    return item


@router.get("/pve-targets/{target_id}/host-maintenance-credential", response_model=Optional[schemas.HostMaintenanceCredentialOut])
def get_host_maintenance_credential(target_id: uuid.UUID, db: Session = Depends(get_db)):
    return (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target_id)
        .one_or_none()
    )


@router.put("/pve-targets/{target_id}/host-maintenance-credential", response_model=schemas.HostMaintenanceCredentialOut, dependencies=[Depends(require_admin)])
def set_host_maintenance_credential(
    target_id: uuid.UUID, payload: schemas.HostMaintenanceCredentialCreate,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Create or replace the ONE host-maintenance SSH credential for this
    PVE target. This never touches the host itself -- it only stores the
    private key PyXie will use once the matching public key has been
    installed on each node by an administrator (see the W4 deployment
    guide). Deliberately a separate table/endpoint from PVE API
    credentials -- see HostMaintenanceCredential's model docstring."""
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "PVE target not found")

    existing = (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target_id)
        .one_or_none()
    )
    encrypted = encrypt_secret(payload.ssh_private_key)
    if existing is None:
        existing = HostMaintenanceCredential(
            pve_target_id=target_id,
            ssh_username=payload.ssh_username,
            ssh_port=payload.ssh_port,
            encrypted_private_key=encrypted,
            status="untested",
            created_by=user.email,
        )
        db.add(existing)
    else:
        existing.ssh_username = payload.ssh_username
        existing.ssh_port = payload.ssh_port
        existing.encrypted_private_key = encrypted
        existing.status = "untested"
        existing.last_validated_at = None

    write_audit_event(
        db,
        event_category="credential",
        event_type="host_maintenance_credential.set",
        actor=user.email, actor_type="user",
        provider_id=target.provider_id,
        metadata={"pve_target_id": str(target_id), "ssh_username": payload.ssh_username},
    )
    db.commit()
    db.refresh(existing)
    return existing


@router.post("/pve-targets/{target_id}/host-maintenance-credential/generate", response_model=schemas.HostMaintenanceCredentialOut, dependencies=[Depends(require_admin)])
def generate_host_maintenance_credential(
    target_id: uuid.UUID, force: bool = False,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Generates a fresh ed25519 keypair server-side for the 'Connect a
    Host' flow. The private key is encrypted at rest and NEVER returned by
    this or any other endpoint -- only the public key ever leaves the
    server, via the kit download below, which is exactly what an admin
    needs to authorize on each node. Regenerating an existing credential
    orphans every already-provisioned node's authorized_keys (they'd need
    re-provisioning with the new public key), so this refuses to silently
    replace one unless force=true."""
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "PVE target not found")

    existing = (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target_id)
        .one_or_none()
    )
    if existing is not None and not force:
        raise HTTPException(
            409,
            "a host-maintenance credential already exists for this target -- regenerating it will "
            "orphan every already-provisioned node (they'd need install.sh re-run with the new public "
            "key). Pass force=true to proceed anyway.",
        )

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    encrypted = encrypt_secret(private_pem)

    if existing is None:
        existing = HostMaintenanceCredential(
            pve_target_id=target_id, ssh_username="pyxie-hostmaint", ssh_port=22,
            encrypted_private_key=encrypted, status="untested", created_by=user.email,
        )
        db.add(existing)
    else:
        existing.encrypted_private_key = encrypted
        existing.status = "untested"
        existing.last_validated_at = None
        existing.last_seen_wrapper_version = None
        existing.last_seen_contract_version = None

    write_audit_event(
        db, event_category="credential", event_type="host_maintenance_credential.generated",
        actor=user.email, actor_type="user", provider_id=target.provider_id,
        metadata={"pve_target_id": str(target_id), "regenerated": existing is not None},
    )
    db.commit()
    db.refresh(existing)
    return existing


@router.get("/pve-targets/{target_id}/host-maintenance-kit")
def download_host_maintenance_kit(target_id: uuid.UUID, db: Session = Depends(get_db)):
    """Bundles the static W4 provisioning templates together with THIS
    target's actual generated public key into a downloadable tar.gz. The
    private key never leaves the server -- only ever the public key,
    which is exactly what install.sh needs to authorize on a node."""
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "PVE target not found")
    cred = (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target_id)
        .one_or_none()
    )
    if cred is None:
        raise HTTPException(409, "no host-maintenance credential generated for this target yet -- generate one first")

    private_pem = decrypt_secret(cred.encrypted_private_key)
    public_line = derive_public_key_line(private_pem)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fname in HOST_MAINTENANCE_KIT_FILES:
            tar.add(f"{HOST_MAINTENANCE_KIT_DIR}/{fname}", arcname=f"pyxie-hostmaint-kit/{fname}")
        pubkey_bytes = (public_line + "\n").encode()
        info = tarfile.TarInfo(name="pyxie-hostmaint-kit/pyxie-hostmaint-key.pub")
        info.size = len(pubkey_bytes)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(pubkey_bytes))
    buf.seek(0)

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in target.name)
    return StreamingResponse(
        buf, media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename=pyxie-hostmaint-kit-{safe_name}.tar.gz"},
    )


@router.get("/pve-targets/{target_id}/host-maintenance-uninstall-script")
def download_host_maintenance_uninstall_script(target_id: uuid.UUID, db: Session = Depends(get_db)):
    """Single-file download of uninstall.sh -- the same generic script for
    any node in this target (it doesn't need the public key, unlike
    install.sh), so no tarball/templating needed. Run on a node to fully
    remove the pyxie-hostmaint identity/wrapper/sudoers from that host;
    independent of (and usually paired with) the app-side DELETE
    /api/nodes/{id}/ssh-host-key 'Disconnect' action."""
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "PVE target not found")

    with open(f"{HOST_MAINTENANCE_KIT_DIR}/uninstall.sh", "rb") as f:
        content = f.read()

    return StreamingResponse(
        io.BytesIO(content), media_type="text/x-shellscript",
        headers={"Content-Disposition": "attachment; filename=uninstall.sh"},
    )


@router.post("/pve-targets/{target_id}/test-connection", dependencies=[Depends(require_admin)])
def test_connection(target_id: uuid.UUID, db: Session = Depends(get_db)):
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "pve target not found")
    result = run_discovery(db, target, actor="user")
    write_audit_event(
        db,
        event_category="provider",
        event_type="pve_target.connection_tested",
        actor="user",
        actor_type="user",
        site_id=target.site_id,
        provider_id=target.provider_id,
        result="success" if result.get("status") == "ok" else "failure",
    )
    return result


@router.post("/pve-targets/{target_id}/discover", dependencies=[Depends(require_admin)])
def trigger_discovery(target_id: uuid.UUID, db: Session = Depends(get_db)):
    target = db.query(PveTarget).filter(PveTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "pve target not found")
    return run_discovery(db, target, actor="user")
