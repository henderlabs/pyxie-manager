"""New operation_type: workload.resize -- the Recommendations page's
'Apply' action for a rightsizing suggestion. Shuts the guest down (if
running), reconfigures cores/memory, starts it back up (if it was running
before), verifies the read-back config matches. Enabled immediately since
it composes three already-approved, already-enabled primitives
(workload.shutdown/start's PVE calls + a plain config PUT) rather than
introducing a new class of PVE mutation.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
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
                "id": "workload.resize",
                "category": "lifecycle",
                "description": "Apply a rightsizing suggestion: change a workload's vCPU/RAM allocation, powering it off and back on as needed.",
                "stages": [
                    "dry_run", "awaiting_approval", "revalidating",
                    "shutting_down", "reconfiguring", "starting_up",
                    "verifying", "audit",
                ],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM operation_types WHERE id = 'workload.resize'")
