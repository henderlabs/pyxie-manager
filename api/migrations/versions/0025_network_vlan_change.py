"""New operation_type: workload.network_vlan_change -- reassign which VLAN
a workload's NIC is on, from the new Network tab.

Not gated by a capability grant (unlike protection.backup_membership) --
per-NIC VLAN tag is a core PVE feature on every cluster, not an optional
provider-specific one, so this follows workload.resize's simpler pattern
(gated only by require_admin at the API layer).

Same Safety Contract as every other PVE write, but the NIC config PUT is
synchronous (no PVE task/UPID) and hot-pluggable on a running guest, so
there's no monitoring stage and no shutdown/start stages either -- same
shape as protection.backup_membership.

Deliberately scoped to per-workload NIC VLAN tag only, NOT host-level
bridge/VLAN-aware config (that's a pending-changes/reload model where a
bad apply can sever the node's own network connectivity -- deliberately
kept read-only, no write path exists for it).

Revision ID: 0025
Revises: 54e735af2025
Create Date: 2026-09-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "54e735af2025"
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
                "id": "workload.network_vlan_change",
                "category": "network",
                "description": "Reassign which VLAN a workload's NIC is on.",
                "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM operation_types WHERE id = 'workload.network_vlan_change'")
