"""Write-capability safety foundation (Stage W0): operation_types,
operations, resource_locks. No operation_type is enabled here except
vm.live_migrate (Stage W1) -- the rest are seeded/reserved rows so their
final shape doesn't require a schema change later, matching the same
"seed the row, wire the workflow later" pattern already used for the
protection provider categories.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_types",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("stages", postgresql.JSONB, nullable=False),
        sa.Column("default_rollback_classification", sa.String, nullable=False, server_default="unknown"),
        sa.Column("required_credential_slot", sa.String, nullable=False, server_default="maintenance"),
        sa.Column("requires_individual_approval", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_type_id", sa.String, sa.ForeignKey("operation_types.id"), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("maintenance_plans.id"), nullable=True),
        sa.Column("parent_operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=True),
        sa.Column("workload_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workloads.id"), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("stage", sa.String, nullable=True),
        sa.Column("stages_completed", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("stages_pending", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("context", postgresql.JSONB, nullable=True),
        sa.Column("dry_run_result", postgresql.JSONB, nullable=True),
        sa.Column("precondition_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("pve_upid", sa.String, nullable=True),
        sa.Column("pve_task_result", postgresql.JSONB, nullable=True),
        sa.Column("verification_result", postgresql.JSONB, nullable=True),
        sa.Column("rollback_classification", sa.String, nullable=True),
        sa.Column("blocking_safety_rules", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("approved_by", sa.String, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_index("ix_operations_correlation_id", "operations", ["correlation_id"])
    op.create_index("ix_operations_node_id", "operations", ["node_id"])
    op.create_index("ix_operations_workload_id", "operations", ["workload_id"])

    op.create_table(
        "resource_locks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_type", sa.String, nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("reason", sa.String, nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_resource_locks_active",
        "resource_locks",
        ["resource_type", "resource_id"],
        postgresql_where=sa.text("released_at IS NULL"),
    )

    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String),
        sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean),
        sa.column("enabled", sa.Boolean),
    )

    common_stages = [
        "dry_run",
        "awaiting_approval",
        "revalidating",
        "executing",
        "monitoring",
        "verifying",
        "audit",
    ]

    op.bulk_insert(
        operation_types,
        [
            {
                "id": "vm.live_migrate",
                "category": "migration",
                "description": "Live-migrate one VM from its current node to a selected destination node.",
                "stages": common_stages,
                "default_rollback_classification": "auto_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
            {
                "id": "node.evacuate",
                "category": "migration",
                "description": "Evacuate every eligible workload off one node, one at a time, reassessing cluster state between each.",
                "stages": ["dry_run", "awaiting_approval", "revalidating", "evacuating", "verifying", "audit"],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
            {
                "id": "workload.shutdown",
                "category": "lifecycle",
                "description": "Graceful guest shutdown with timeout. Forced stop is a separate, higher-risk operation type, never an automatic fallback.",
                "stages": common_stages,
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
            {
                "id": "workload.start",
                "category": "lifecycle",
                "description": "Start a stopped guest.",
                "stages": common_stages,
                "default_rollback_classification": "auto_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
            {
                "id": "workload.force_stop",
                "category": "lifecycle",
                "description": "Higher-risk forced stop. Never triggered automatically as a shutdown-timeout fallback.",
                "stages": common_stages,
                "default_rollback_classification": "irreversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
            {
                "id": "host.update",
                "category": "patching",
                "description": "Host package/update management using the maintenance credential, with full repo/update preflight.",
                "stages": common_stages,
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
            {
                "id": "host.reboot",
                "category": "reboot",
                "description": "Controlled host reboot with pre-checks and post-reboot verification. A node that fails to return stops the workflow -- no automatic BMC power cycling.",
                "stages": common_stages,
                "default_rollback_classification": "irreversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
            {
                "id": "maintenance.run",
                "category": "maintenance",
                "description": "End-to-end explicitly-approved maintenance workflow combining evacuate -> patch -> reboot -> validate -> optional restore-placement.",
                "stages": [
                    "preflight",
                    "awaiting_approval",
                    "evacuate",
                    "verify_evacuation",
                    "patch",
                    "verify_patch",
                    "reboot",
                    "verify_node",
                    "verify_cluster",
                    "restore_placement",
                    "verify_workloads",
                    "audit",
                ],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": False,
            },
        ],
    )

    safety_rules = sa.table(
        "safety_rules",
        sa.column("id", sa.String),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("category", sa.String),
    )
    op.bulk_insert(
        safety_rules,
        [
            {
                "id": "SAFE-CRS-001",
                "title": "CRS/affinity rule violation",
                "description": "Destination would violate a configured PVE HA/CRS affinity or anti-affinity rule.",
                "category": "migration",
            },
            {
                "id": "SAFE-CPU-001",
                "title": "CPU compatibility risk",
                "description": "Source and destination node CPU models/flags may not be migration-compatible for this workload's configured CPU type.",
                "category": "migration",
            },
            {
                "id": "SAFE-LOCK-001",
                "title": "Resource lock held",
                "description": "The target resource is already locked by another in-flight PyXie operation.",
                "category": "execution",
            },
            {
                "id": "SAFE-UPDATE-001",
                "title": "Host update preflight failed",
                "description": "Repository health, package manager lock, disk space, or held-package preflight checks failed before patching.",
                "category": "patching",
            },
        ],
    )


def downgrade():
    op.drop_table("resource_locks")
    op.drop_index("ix_operations_workload_id", table_name="operations")
    op.drop_index("ix_operations_node_id", table_name="operations")
    op.drop_index("ix_operations_correlation_id", table_name="operations")
    op.drop_index("ix_operations_status", table_name="operations")
    op.drop_table("operations")
    op.drop_table("operation_types")
    op.execute(
        "DELETE FROM safety_rules WHERE id IN "
        "('SAFE-CRS-001','SAFE-CPU-001','SAFE-LOCK-001','SAFE-UPDATE-001')"
    )
