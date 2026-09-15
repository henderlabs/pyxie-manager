"""Explicit node maintenance-mode state, modeled after how vSphere/Hyper-V/
Nutanix all do it: entering maintenance mode evacuates the node (live
migrate what can move, shut down what can't) and marks it out of service;
exiting just makes it available again (workloads that were shut down for
entry get restarted, but nothing is force-migrated back -- that mirrors
Hyper-V's separate, optional "fail roles back" step, not something this
does automatically). host.update and host.reboot both now hard-require
maintenance_mode -- no host gets patched or rebooted without first being
explicitly taken out of service -- and a maintenance.run sets/clears the
same flag around its own existing
evacuate/patch/reboot/restore sequence so nothing about that combined
workflow's behavior changes.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("maintenance_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("nodes", sa.Column("maintenance_mode_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nodes", sa.Column("maintenance_reason", sa.Text(), nullable=True))
    op.add_column("nodes", sa.Column("maintenance_mode_by", sa.String(), nullable=True))

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
                "id": "node.enter_maintenance",
                "category": "maintenance",
                "description": "Evacuate a node (live-migrate what can move, shut down in place what can't) and mark it in maintenance mode. Independent of any specific job -- physical hardware work and a patch cycle both just need this.",
                "stages": ["dry_run", "awaiting_approval", "revalidating", "evacuating", "verifying", "audit"],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
            {
                "id": "node.exit_maintenance",
                "category": "maintenance",
                "description": "Restart anything shut down for maintenance entry and mark the node available again. Does not force-migrate anything back -- workloads live-migrated off stay wherever they were sent unless separately moved.",
                "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                "default_rollback_classification": "auto_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
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
                "id": "SAFE-MAINTMODE-001",
                "title": "Node not in maintenance mode",
                "description": "host.update and host.reboot both refuse to run unless the node has already been explicitly put into maintenance mode.",
                "category": "patching",
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM safety_rules WHERE id = 'SAFE-MAINTMODE-001'")
    op.execute("DELETE FROM operation_types WHERE id IN ('node.enter_maintenance', 'node.exit_maintenance')")
    op.drop_column("nodes", "maintenance_mode_by")
    op.drop_column("nodes", "maintenance_reason")
    op.drop_column("nodes", "maintenance_mode_since")
    op.drop_column("nodes", "maintenance_mode")
