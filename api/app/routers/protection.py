import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pyxie_core.audit import write_audit_event
from pyxie_core.crypto import encrypt_secret
from pyxie_core.models import (
    CapabilityGrant,
    Cluster,
    ProtectionCredential,
    ProtectionResult,
    ProtectionTarget,
    Provider,
    ProviderCapability,
    Workload,
)
from pyxie_core.protection import sync_pbs_protection
from pyxie_core.protection_workflow import ProtectionWorkflowError, dry_run_backup_membership, list_pbs_backup_jobs

from ..auth_deps import get_current_user, require_admin
from ..deps import get_db

router = APIRouter(prefix="/api/protection", tags=["protection"], dependencies=[Depends(get_current_user)])


class ProtectionTargetCreate(BaseModel):
    site_id: uuid.UUID
    name: str
    hostname: str
    api_port: int = 8007
    tls_verify: bool = True
    token_user: str
    token_id: str
    token_secret: str


@router.get("/targets")
def list_targets(db: Session = Depends(get_db)):
    targets = db.query(ProtectionTarget).all()
    out = []
    for t in targets:
        provider = db.query(Provider).filter(Provider.id == t.provider_id).one()
        out.append(
            {
                "id": str(t.id),
                "site_id": str(t.site_id),
                "provider_id": str(t.provider_id),
                "provider_type": provider.provider_type,
                "name": t.name,
                "hostname": t.hostname,
                "api_port": t.api_port,
                "tls_verify": t.tls_verify,
                "connection_health": provider.connection_health,
                "last_error": provider.last_error,
            }
        )
    return out


@router.post("/targets/pbs", dependencies=[Depends(require_admin)])
def create_pbs_target(payload: ProtectionTargetCreate, db: Session = Depends(get_db)):
    provider = (
        db.query(Provider)
        .filter(Provider.category_id == "protection", Provider.provider_type == "pbs", Provider.instance_name == payload.name)
        .one_or_none()
    )
    if provider is None:
        provider = Provider(
            category_id="protection",
            provider_type="pbs",
            name="Proxmox Backup Server",
            instance_name=payload.name,
            enabled=True,
            contract_version=1,
            connection_health="unknown",
            implementation_status="live_tested",
            live_validation_status="tested",
        )
        db.add(provider)
        db.flush()
        for cap_id in (
            "protection.status.read", "protection.jobs.active.read", "protection.schedule.read",
            "protection.jobs.membership.write",
        ):
            db.add(ProviderCapability(provider_id=provider.id, capability_id=cap_id))
            db.add(CapabilityGrant(provider_id=provider.id, capability_id=cap_id, scope_type="site", scope_id=payload.site_id, granted_by="system"))

    target = ProtectionTarget(
        site_id=payload.site_id, provider_id=provider.id, name=payload.name, hostname=payload.hostname,
        api_port=payload.api_port, tls_verify=payload.tls_verify,
    )
    db.add(target)
    db.flush()

    cred = ProtectionCredential(
        protection_target_id=target.id, slot_name="inventory", token_user=payload.token_user,
        token_id=payload.token_id, encrypted_secret=encrypt_secret(payload.token_secret), status="untested",
    )
    db.add(cred)

    write_audit_event(
        db, event_category="provider", event_type="protection_target.created", actor="user", actor_type="user",
        site_id=payload.site_id, provider_id=provider.id,
        state_after={"name": payload.name, "hostname": payload.hostname},
    )
    db.commit()
    db.refresh(target)
    return {"id": str(target.id)}


@router.post("/targets/{target_id}/sync", dependencies=[Depends(require_admin)])
def sync_target(target_id: uuid.UUID, db: Session = Depends(get_db)):
    target = db.query(ProtectionTarget).filter(ProtectionTarget.id == target_id).one_or_none()
    if target is None:
        raise HTTPException(404, "protection target not found")
    return sync_pbs_protection(db, target, actor="user")


@router.get("/backup-jobs")
def list_backup_jobs(db: Session = Depends(get_db)):
    """Every vzdump job, on every known cluster, whose target storage is
    PBS-backed -- the only scope this endpoint (and its membership-editing
    counterpart below) ever touches. A job backed by any other storage
    type is invisible here, deliberately: this is a PBS-specific control,
    not a general backup-job editor."""
    out = []
    for cluster in db.query(Cluster).filter(Cluster.is_missing.is_(False)).all():
        for job in list_pbs_backup_jobs(db, cluster):
            job["cluster_id"] = str(cluster.id)
            job["cluster_name"] = cluster.name
            out.append(job)
    return out


class BackupMembershipDryRunRequest(BaseModel):
    cluster_id: uuid.UUID
    job_id: str
    select_all: bool = False
    add_vmids: list[int] = []
    remove_vmids: list[int] = []


@router.post("/backup-jobs/dry-run", dependencies=[Depends(require_admin)])
def create_backup_membership_dry_run(
    payload: BackupMembershipDryRunRequest, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    cluster = db.query(Cluster).filter(Cluster.id == payload.cluster_id).one_or_none()
    if cluster is None:
        raise HTTPException(404, "cluster not found")
    if not payload.select_all and not payload.add_vmids and not payload.remove_vmids:
        raise HTTPException(400, "specify select_all, or at least one of add_vmids/remove_vmids")
    try:
        op = dry_run_backup_membership(
            db, cluster, payload.job_id,
            select_all=payload.select_all, add_vmids=payload.add_vmids, remove_vmids=payload.remove_vmids,
            actor=user.email,
        )
    except ProtectionWorkflowError as exc:
        raise HTTPException(400, str(exc))
    return {"id": str(op.id), "status": op.status, "dry_run_result": op.dry_run_result}


@router.get("/results")
def list_results(db: Session = Depends(get_db)):
    results = db.query(ProtectionResult).all()
    out = []
    for r in results:
        wl = db.query(Workload).filter(Workload.id == r.workload_id).one_or_none()
        out.append(
            {
                "workload_id": str(r.workload_id),
                "vmid": wl.vmid if wl else None,
                "name": wl.name if wl else None,
                "provider_id": str(r.provider_id),
                "protected": r.protected,
                "last_successful_job_at": r.last_successful_job_at.isoformat() if r.last_successful_job_at else None,
                "last_restore_point_at": r.last_restore_point_at.isoformat() if r.last_restore_point_at else None,
                "sla_defined": r.sla_defined,
                "sla_compliant": r.sla_compliant,
                "restore_verified": r.restore_verified,
                "confidence": r.confidence,
                "last_error": r.last_error,
            }
        )
    return out
