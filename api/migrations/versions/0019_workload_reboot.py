"""New operation_type: workload.reboot -- Start/Stop/Restart buttons on the
Workloads page. PVE's own guest reboot (ACPI shutdown
signal, then PVE starts it back up as one task) rather than an app-level
shutdown-then-start pair -- same Safety Contract, same credential slot and
approval requirement as shutdown/start/force_stop, and reuses their
existing SAFE-DOWNTIME-001/SAFE-HA-001 safety rules rather than adding new
ones (see lifecycle_workflow.py's _dry_run check).

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
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
    op.bulk_insert(
        operation_types,
        [
            {
                "id": "workload.reboot",
                "category": "lifecycle",
                "description": "Reboot a running guest: graceful ACPI shutdown, then PVE starts it back up as a single task.",
                "stages": [
                    "dry_run", "awaiting_approval", "revalidating",
                    "executing", "monitoring", "verifying", "audit",
                ],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM operation_types WHERE id = 'workload.reboot'")
