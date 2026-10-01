"""Failover selection: which cluster members may serve as PVE API endpoints,
and which one is preferred.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("failover_enabled", sa.Boolean, nullable=False, server_default=sa.true()))
    op.add_column(
        "pve_targets",
        sa.Column("preferred_node_id", UUID(as_uuid=True), sa.ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade():
    op.drop_column("pve_targets", "preferred_node_id")
    op.drop_column("nodes", "failover_enabled")
