"""New operation_type: cluster.rebalance -- unifies "Balance Load" into
the same reviewable/editable migrate_plan + Safety Contract every other
batch migration workflow already uses (node.enter_maintenance/
maintenance.run/node.evacuate), instead of its old one-off "propose N
moves, apply each individually, no single approval, no cancel" flow.
Same stage shape as node.evacuate -- it's a batch of migrations with no
patch/reboot phase, just evacuate/verify/audit.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
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
                "id": "cluster.rebalance",
                "category": "migration",
                "description": "Move VMs cluster-wide (or on selected node(s)) onto better-scoring destinations, one at a time, as one reviewable/editable plan.",
                "stages": [
                    "dry_run", "awaiting_approval", "revalidating",
                    "evacuating", "verifying", "audit",
                ],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM operation_types WHERE id = 'cluster.rebalance'")
