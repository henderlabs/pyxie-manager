"""Stage W4: real host package-update execution via SSH, through the
restricted /usr/local/sbin/pyxie-maint wrapper (see
host_maintenance_client.py and deploy/host-maintenance/ for the host-side
artifacts). This replaces the earlier preflight-only version of this
module, which existed because PVE's own REST API has no endpoint to apply
updates -- only to list them.

Full sequence, matching the Safety Contract used everywhere else in this
codebase:
  dry-run (version check -> status -> refresh -> plan, PERSISTED into
  op.context) -> awaiting_approval -> approved -> revalidate (refresh +
  recompute the plan, compare against what was actually approved -- if it
  materially changed, BLOCK rather than proceed on stale consent) -> lock
  the node -> refresh + recompute ONE MORE TIME immediately before apply
  (closes the window between revalidation and lock acquisition too) ->
  apply through the wrapper -> capture its output/exit status -> verify
  independently (status again, not trusted from apply's own claim) ->
  determine whether a reboot is required -> audit the full before/after
  package state.

Never installs anything itself outside of an explicitly approved
operation -- Stage W4 does not schedule or trigger itself.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import rollback
from .credentials import load_host_maintenance_credentials, load_pve_credentials
from .host_maintenance_client import (
    HostMaintenanceClient,
    HostMaintenanceConnectionError,
    HostMaintenanceProtocolError,
    MutationsDisabledError,
    SUPPORTED_CONTRACT_VERSION,
    sanitize_captured_output,
)
from .locks import LockContention, acquire_lock, release_locks_for_operation
from .operation_log import HostLogStreamer, log_stage
from .maintenance import _quorum_after_removal
from .models import Cluster, HostMaintenanceCredential, Node, Operation, PveTarget
from .pve_client import PveClient
from .operations_engine import (
    OperationError,
    block_operation,
    create_operation,
    enter_stage,
    fail_operation,
)

now = lambda: datetime.now(timezone.utc)

MIN_FREE_DISK_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB -- refuse to plan an update below this


class HostUpdateWorkflowError(Exception):
    pass


def _load_context(db: Session, node: Node):
    cluster = db.query(Cluster).filter(Cluster.id == node.cluster_id).one()
    target = db.query(PveTarget).filter(PveTarget.id == cluster.pve_target_id).one()
    return cluster, target


def _package_key(pkg: dict) -> tuple:
    return (pkg.get("package"), pkg.get("new_version"))


def _packages_materially_differ(approved: list, fresh: list) -> bool:
    return {_package_key(p) for p in approved} != {_package_key(p) for p in fresh}


def dry_run_host_update(
    db: Session, node: Node, *, actor: str, parent_operation_id=None, correlation_id=None, check_only: bool = False,
) -> Operation:
    """check_only=True is the "Check for Updates" button: a pure look, never
    something to approve, so it (1) falls back to the read-only PVE API
    package list when the SSH wrapper isn't connected for this host, instead
    of blocking on a thing the check doesn't need, and (2) completes
    immediately when there is nothing to apply, instead of parking an
    approval for a no-op. Apply Updates and Full Maintenance call this with
    check_only=False and behave exactly as before (they genuinely need the
    wrapper, and approval semantics are unchanged)."""
    cluster, target = _load_context(db, node)
    op = create_operation(
        db, "host.update", cluster_id=cluster.id, node_id=node.id,
        parent_operation_id=parent_operation_id, correlation_id=correlation_id,
        context={"node": node.name}, created_by=actor,
    )

    reasons: list[str] = []
    blocking_rules: list[str] = []

    def _blocked(extra_result: dict | None = None) -> Operation:
        dry_run_result = {
            "node": node.name, "eligible": False,
            "reasons": reasons, "blocking_safety_rules": blocking_rules,
            **(extra_result or {}),
        }
        return enter_stage(
            db, op, status="blocked", stage="dry_run",
            dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, actor=actor,
        )

    def _api_list_only() -> Operation:
        """Read-only fallback for Check for Updates on a host the wrapper
        can't reach: the pending list from PVE's own API (maintenance
        token, same source as the Pending-updates view). Completes
        immediately as information -- there is nothing to approve, because
        applying needs the wrapper."""
        try:
            creds = load_pve_credentials(db, target, "maintenance")
            with PveClient(creds) as api:
                raw = api.node_apt_updates(node.name)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"the PVE API package listing failed too: {exc}")
            return _blocked()
        packages = [
            {"package": r.get("Package"), "current_version": r.get("OldVersion"), "new_version": r.get("Version")}
            for r in raw
        ]
        note = (
            "Read through the PVE API only -- this host is not connected for patching, so these can be reviewed "
            "but not applied from PyXie. Connect it under Platform > Credentials > Host maintenance to apply updates."
        )
        info_reasons = list(reasons) + [note]
        if not packages:
            info_reasons.append("no pending package updates on this host right now")
        dry_run_result = {
            "node": node.name, "eligible": True, "source": "pve_api", "apply_available": False,
            "planned_packages": packages, "planned_package_count": len(packages),
            "plan_computed_at": now().isoformat(),
            "reasons": info_reasons, "blocking_safety_rules": [],
        }
        done = enter_stage(
            db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result,
            context={"node": node.name, "check_only": True, "source": "pve_api"}, actor=actor,
        )
        return enter_stage(db, done, status="completed", stage="audit", actor=actor)

    cred_row = (
        db.query(HostMaintenanceCredential)
        .filter(HostMaintenanceCredential.pve_target_id == target.id)
        .one_or_none()
    )
    if cred_row is None:
        reasons.append("no host-maintenance SSH credential configured for this cluster -- see Platform > Credentials")
        blocking_rules.append("SAFE-HOSTCRED-001")
        return _api_list_only() if check_only else _blocked()

    if not node.management_ip:
        reasons.append(f"no management_ip on record for {node.name} yet -- run inventory discovery first")
        blocking_rules.append("SAFE-HOSTCRED-001")
        return _api_list_only() if check_only else _blocked()

    try:
        hm_creds = load_host_maintenance_credentials(db, target, node)
    except Exception as exc:
        reasons.append(f"could not load host-maintenance credentials: {exc}")
        blocking_rules.append("SAFE-HOSTCRED-001")
        return _api_list_only() if check_only else _blocked()

    try:
        with HostMaintenanceClient(hm_creds) as client:
            version_info = client.version()
            if version_info.get("contract_version") != SUPPORTED_CONTRACT_VERSION:
                reasons.append(
                    f"wrapper contract_version={version_info.get('contract_version')} does not match "
                    f"PyXie's supported version {SUPPORTED_CONTRACT_VERSION} -- update the wrapper on this host"
                )
                blocking_rules.append("SAFE-HOSTCONTRACT-001")
                return _blocked({"wrapper_version": version_info.get("wrapper_version"),
                                  "contract_version": version_info.get("contract_version")})

            status_info = client.status()
            client.refresh()
            plan_info = client.plan()
    except MutationsDisabledError:
        raise  # never expected from a read-only dry-run path; surface loudly if it somehow happens
    except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
        reasons.append(f"could not reach host-maintenance wrapper: {exc}")
        blocking_rules.append("SAFE-HOSTCONN-001")
        return _api_list_only() if check_only else _blocked()

    cred_row.status = "valid"
    cred_row.last_validated_at = now()
    cred_row.last_seen_wrapper_version = version_info.get("wrapper_version")
    cred_row.last_seen_contract_version = version_info.get("contract_version")
    db.commit()

    packages = plan_info.get("packages", [])
    disk_free_bytes = status_info.get("disk_free_bytes")

    if disk_free_bytes is not None and disk_free_bytes < MIN_FREE_DISK_BYTES:
        reasons.append(
            f"only {disk_free_bytes} bytes free on / -- refusing to plan an update below the "
            f"{MIN_FREE_DISK_BYTES} byte safety floor"
        )
        blocking_rules.append("SAFE-DISK-001")

    if not packages:
        reasons.append("no pending package updates on this host right now -- nothing to apply")

    plan_computed_at = plan_info.get("computed_at") or now().isoformat()

    dry_run_result = {
        "node": node.name,
        "wrapper_version": version_info.get("wrapper_version"),
        "contract_version": version_info.get("contract_version"),
        "kernel_version": status_info.get("kernel_version"),
        "pve_version": status_info.get("pve_version"),
        "disk_free_bytes": disk_free_bytes,
        "reboot_required_hint": status_info.get("reboot_required"),
        "planned_packages": packages,
        "planned_package_count": len(packages),
        "plan_computed_at": plan_computed_at,
        "eligible": len(blocking_rules) == 0,
        "reasons": reasons,
        "blocking_safety_rules": blocking_rules,
    }
    context = {
        "node": node.name,
        "planned_packages": packages,
        "plan_computed_at": plan_computed_at,
        "before_status": status_info,
    }

    if blocking_rules:
        return enter_stage(
            db, op, status="blocked", stage="dry_run",
            dry_run_result=dry_run_result, blocking_safety_rules=blocking_rules, context=context, actor=actor,
        )
    op = enter_stage(db, op, status="dry_run", stage="dry_run", dry_run_result=dry_run_result, context=context, actor=actor)
    if check_only and not packages:
        # Up to date: a look that found nothing has nothing to approve.
        return enter_stage(db, op, status="completed", stage="audit", actor=actor)
    return enter_stage(db, op, status="awaiting_approval", stage="awaiting_approval", actor=actor)


def approve(db: Session, op: Operation, *, approved_by: str) -> Operation:
    from .operations_engine import approve_operation
    return approve_operation(db, op, approved_by=approved_by)


def execute_host_update(db: Session, operation_id) -> Operation:
    """Resumable, same pattern as every other Stage W2-W6 workflow: picks
    back up from op.status/op.pve_task_result rather than re-issuing
    anything already done."""
    op = db.query(Operation).filter(Operation.id == operation_id).one_or_none()
    if op is None:
        raise HostUpdateWorkflowError(f"operation {operation_id} not found")
    if op.status not in ("approved", "revalidating", "executing", "monitoring", "verifying"):
        raise OperationError(f"operation {op.id} is not in an executable state (status={op.status})")

    node = db.query(Node).filter(Node.id == op.node_id).one()
    cluster, target = _load_context(db, node)
    ctx = op.context or {}
    approved_packages = ctx.get("planned_packages", [])

    try:
        hm_creds = load_host_maintenance_credentials(db, target, node)
    except Exception as exc:
        return fail_operation(db, op, error=f"could not load host-maintenance credentials: {exc}")

    try:
        if op.status == "approved":
            op = enter_stage(db, op, status="revalidating", stage="revalidating")
            log_stage(op.id, f"Approved. Re-checking {node.name}: maintenance mode, package lists, and that the plan is unchanged.")
            if not node.maintenance_mode:
                # Checking for updates never required this (dry_run_host_update
                # has no such gate) -- only actually applying them does. Matches
                # the same block_operation-with-just-the-rule-id pattern as
                # every other revalidation-stage block in this function (e.g.
                # SAFE-HOSTCONN-001 below); the safety_rules table row is what
                # carries the human-readable explanation.
                return block_operation(db, op, blocking_safety_rules=["SAFE-MAINTMODE-001"], actor="system")
            try:
                with HostMaintenanceClient(hm_creds) as client:
                    client.refresh()
                    fresh_plan = client.plan()
            except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
                return block_operation(db, op, blocking_safety_rules=["SAFE-HOSTCONN-001"], actor="system")

            if _packages_materially_differ(approved_packages, fresh_plan.get("packages", [])):
                return block_operation(
                    db, op, blocking_safety_rules=["SAFE-PLAN-CHANGED-001"], actor="system",
                )

            try:
                acquire_lock(db, resource_type="node", resource_id=node.id, operation_id=op.id, reason="host.update", ttl_seconds=3600)
            except LockContention:
                return block_operation(db, op, blocking_safety_rules=["SAFE-LOCK-001"], actor="system")
            op = enter_stage(db, op, status="executing", stage="executing")

        if op.status == "executing" and not op.pve_task_result:
            # One more refresh + recompute immediately before the actual
            # apply call -- closes the window between revalidation and lock
            # acquisition, per the approved architecture, not just the
            # window between dry-run and approval.
            try:
                with HostMaintenanceClient(hm_creds) as client:
                    client.refresh()
                    final_plan = client.plan()
                    if _packages_materially_differ(approved_packages, final_plan.get("packages", [])):
                        release_locks_for_operation(db, op.id)
                        return block_operation(db, op, blocking_safety_rules=["SAFE-PLAN-CHANGED-001"], actor="system")

                    before_status = client.status()
                    log_stage(op.id, f"Plan unchanged ({len(approved_packages)} package(s)). Starting apt dist-upgrade on {node.name}.")
                    streamer = None
                    try:
                        capabilities = (client.version() or {}).get("capabilities") or []
                    except Exception:  # noqa: BLE001 -- live output is optional
                        capabilities = []
                    if "log" in capabilities:
                        try:
                            baseline = client.log()
                        except Exception:  # noqa: BLE001
                            baseline = None
                        streamer = HostLogStreamer(op.id, HostMaintenanceClient, hm_creds, baseline).start()
                    else:
                        log_stage(op.id, "Live host output is not available: this host's pyxie-maint wrapper is older than 1.1.0. "
                                         "The update still runs; its output appears when it finishes.")
                    try:
                        apply_result = client.apply()
                    finally:
                        if streamer is not None:
                            streamer.stop()
                    log_stage(op.id, f"apt finished (exit {apply_result.get('exit_status')}). Verifying against the host's live package state.")
                    if "log_tail" in apply_result:
                        # Defense in depth -- the wrapper already sanitizes
                        # its own captured output, but never trust a
                        # remote process's output as already-safe before
                        # it's persisted/displayed.
                        apply_result["log_tail"] = sanitize_captured_output(apply_result["log_tail"])
            except MutationsDisabledError as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=str(exc))
            except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"apply failed: {exc}")

            op.pve_task_result = {"apply_result": apply_result, "before_status": before_status}
            db.commit()
            op = enter_stage(db, op, status="verifying", stage="verifying")

        if op.status == "verifying":
            try:
                with HostMaintenanceClient(hm_creds) as client:
                    after_status = client.verify()
                    # A fresh plan() re-read (not just trusting the
                    # wrapper's own apply_result.success) is what actually
                    # proves the approved packages got installed --
                    # verification previously only checked that the
                    # wrapper self-reported success, and never required
                    # the approved package set to actually disappear from
                    # the upgradable list.
                    after_plan = client.plan()
            except (HostMaintenanceConnectionError, HostMaintenanceProtocolError) as exc:
                release_locks_for_operation(db, op.id)
                return fail_operation(db, op, error=f"post-apply verification unreachable: {exc}")

            task_result = op.pve_task_result or {}
            before_status = task_result.get("before_status", {})
            apply_result = task_result.get("apply_result", {})
            apply_succeeded = bool(apply_result.get("success"))
            reboot_required = bool(after_status.get("reboot_required"))

            still_pending = sorted(
                {_package_key(p) for p in approved_packages} & {_package_key(p) for p in after_plan.get("packages", [])}
            )

            verification = {
                "before_kernel_version": before_status.get("kernel_version"),
                "after_kernel_version": after_status.get("kernel_version"),
                "before_upgradable_count": before_status.get("upgradable_count"),
                "after_upgradable_count": after_status.get("upgradable_count"),
                "applied_packages": approved_packages,
                "apply_exit_status": apply_result.get("exit_status"),
                "apply_succeeded": apply_succeeded,
                "reboot_required": reboot_required,
                "still_pending_after_apply": [{"package": pkg, "new_version": ver} for pkg, ver in still_pending],
            }
            op.verification_result = verification
            db.commit()

            release_locks_for_operation(db, op.id)
            if not apply_succeeded:
                return fail_operation(db, op, error=f"apply did not report success: {apply_result}")
            if still_pending:
                return fail_operation(
                    db, op,
                    error=f"apply reported success, but {len(still_pending)} approved package(s) still show as "
                          f"upgradable after re-checking live: {still_pending} -- installed versions did not "
                          f"actually change, not trusting the wrapper's self-report alone",
                )

            return enter_stage(
                db, op, status="completed", stage="audit",
                rollback_classification=rollback.MANUAL_REVERSIBLE,
            )

        raise OperationError(f"operation {op.id} reached an unexpected state (status={op.status})")

    except Exception as exc:  # noqa: BLE001
        release_locks_for_operation(db, op.id)
        return fail_operation(db, op, error=f"unexpected error: {exc}")
