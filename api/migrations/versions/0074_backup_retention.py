"""New operation type: protection.backup_retention (change a Proxmox backup job's retention).

Revision ID: 0074
Revises: 0073
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

ID = "protection.backup_retention"


def upgrade():
    t = sa.table(
        "operation_types",
        sa.column("id", sa.String), sa.column("category", sa.String), sa.column("description", sa.Text), sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String), sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean), sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(t, [{"id": ID, "category": "lifecycle", "description": "Change how many backups a Proxmox backup job keeps (its prune-backups setting).",
                        "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                        "default_rollback_classification": "manual_reversible", "required_credential_slot": "maintenance",
                        "requires_individual_approval": True, "enabled": True}])


def downgrade():
    op.execute(f"DELETE FROM resource_locks WHERE operation_id IN (SELECT id FROM operations WHERE operation_type_id = '{ID}')")
    op.execute(f"DELETE FROM operations WHERE operation_type_id = '{ID}'")
    op.execute(f"DELETE FROM operation_types WHERE id = '{ID}'")
