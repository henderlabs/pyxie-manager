"""Protection subsystem: generic contract + the PBS adapter (live-tested
against the lab). Veeam/Commvault are structural-only in this pass -- see
veeam_client.py / commvault_client.py -- and are marked accordingly via
Provider.implementation_status / live_validation_status rather than being
allowed to imply real interoperability.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .audit import write_audit_event
from .crypto import decrypt_secret
from .models import Policy, ProtectionCredential, ProtectionResult, ProtectionTarget, Provider, Workload
from .pbs_client import PbsClient, PbsCredentials
from .pve_client import PveAuthError, PveConnectionError, PveTlsError

DEFAULT_MAX_BACKUP_AGE_HOURS = 48


def _max_backup_age_hours(db: Session) -> int:
    policy = db.query(Policy).filter(Policy.key == "protection.max_backup_age_hours", Policy.scope_type == "organization").one_or_none()
    if policy and isinstance(policy.value, dict) and "hours" in policy.value:
        return int(policy.value["hours"])
    return DEFAULT_MAX_BACKUP_AGE_HOURS


def sync_pbs_protection(db: Session, target: ProtectionTarget, actor: str = "system") -> dict:
    provider = db.query(Provider).filter(Provider.id == target.provider_id).one()
    cred = (
        db.query(ProtectionCredential)
        .filter(ProtectionCredential.protection_target_id == target.id, ProtectionCredential.slot_name == "inventory")
        .one_or_none()
    )
    if cred is None:
        provider.connection_health = "unavailable"
        provider.last_error = "no 'inventory' credential slot configured"
        db.commit()
        return {"status": "failed", "error": provider.last_error}

    creds = PbsCredentials(
        hostname=target.hostname,
        api_port=target.api_port,
        token_user=cred.token_user,
        token_id=cred.token_id,
        token_secret=decrypt_secret(cred.encrypted_secret),
        tls_verify=target.tls_verify,
    )

    max_age = timedelta(hours=_max_backup_age_hours(db))
    now = datetime.now(timezone.utc)
    matched = 0
    unmatched_groups = 0

    try:
        with PbsClient(creds) as client:
            client.version()
            stores = client.datastores()
            latest_backup_by_vmid: dict[tuple[str, str], int] = {}
            for store in stores:
                store_name = store["store"] if isinstance(store, dict) else store
                try:
                    groups = client.groups(store_name) or []
                except Exception:
                    # one datastore being unmounted/unavailable shouldn't
                    # sink the whole sync -- its groups are just absent this
                    # pass, which is exactly what "unmatched" already means.
                    continue
                for group in groups:
                    btype = group.get("backup-type")
                    bid = group.get("backup-id")
                    last_backup = group.get("last-backup")
                    if btype not in ("vm", "ct") or bid is None:
                        continue
                    key = (btype, str(bid))
                    if last_backup and (key not in latest_backup_by_vmid or last_backup > latest_backup_by_vmid[key]):
                        latest_backup_by_vmid[key] = last_backup

        pbs_type_by_workload_type = {"vm": "vm", "lxc": "ct"}
        for wl in db.query(Workload).filter(Workload.is_missing.is_(False)).all():
            pbs_type = pbs_type_by_workload_type.get(wl.type)
            key = (pbs_type, str(wl.vmid))
            last_backup_ts = latest_backup_by_vmid.get(key)

            result = (
                db.query(ProtectionResult)
                .filter(ProtectionResult.workload_id == wl.id, ProtectionResult.provider_id == provider.id)
                .one_or_none()
            )
            if result is None:
                result = ProtectionResult(workload_id=wl.id, provider_id=provider.id)
                db.add(result)

            if last_backup_ts:
                matched += 1
                last_backup_at = datetime.fromtimestamp(last_backup_ts, tz=timezone.utc)
                result.protected = "true"
                result.last_successful_job_at = last_backup_at
                result.last_restore_point_at = last_backup_at
                result.sla_defined = True
                result.sla_compliant = "true" if (now - last_backup_at) <= max_age else "false"
                result.confidence = "high"
                result.last_error = None
            else:
                unmatched_groups += 1
                result.protected = "false"
                result.sla_defined = True
                result.sla_compliant = "false"
                result.confidence = "high"

        provider.connection_health = "connected"
        provider.last_success_at = now
        provider.last_error = None
        cred.status = "valid"
        cred.last_validated_at = now
        db.commit()

        write_audit_event(
            db,
            event_category="protection",
            event_type="protection.sync.completed",
            actor=actor,
            provider_id=provider.id,
            site_id=target.site_id,
            result="success",
            metadata={"matched": matched, "unmatched": unmatched_groups},
        )
        return {"status": "ok", "matched": matched, "unmatched": unmatched_groups}

    except PveTlsError as e:
        return _fail(db, provider, cred, "tls_error", str(e), actor, target)
    except PveAuthError as e:
        return _fail(db, provider, cred, "authentication_failed", str(e), actor, target)
    except PveConnectionError as e:
        return _fail(db, provider, cred, "unavailable", str(e), actor, target)
    except Exception as e:  # noqa: BLE001
        return _fail(db, provider, cred, "unavailable", str(e), actor, target)


def _fail(db, provider, cred, health, message, actor, target):
    db.rollback()
    provider.connection_health = health
    provider.last_error = message
    db.commit()

    # A provider going unreachable doesn't mean its LAST KNOWN status was
    # wrong -- but confidence='high' from the last successful sync should
    # not silently keep looking that trustworthy while the provider stays
    # unreachable for an unknown amount of time afterward. `protected`/
    # `sla_compliant` are left as their last-known value (still the best
    # information anyone has), but confidence downgrades to 'stale' and
    # last_error is recorded on the result itself, not just the provider,
    # so a per-workload row is visibly untrustworthy even if someone only
    # ever looks at Protection results, never Providers.
    stale_count = (
        db.query(ProtectionResult)
        .filter(ProtectionResult.provider_id == provider.id)
        .update({"confidence": "stale", "last_error": message}, synchronize_session=False)
    )
    db.commit()

    write_audit_event(
        db,
        event_category="protection",
        event_type="protection.sync.failed",
        actor=actor,
        provider_id=provider.id,
        site_id=target.site_id,
        result="failure",
        severity="error",
        error=message,
        metadata={"stale_results_marked": stale_count},
    )
    return {"status": "failed", "error": message}
