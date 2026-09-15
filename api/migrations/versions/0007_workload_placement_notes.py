"""Free-text placement note per workload -- PyXie-only metadata (never
synced from/to PVE), so a multi-admin environment has a paper trail for WHY
a tier/sensitivity/downtime value was set a certain way, not just what it
is. Node notes need no schema change -- they reuse the existing generic
policies table (key='placement.notes'), same as node performance/trust
tiers already do.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("placement_notes", sa.Text, nullable=True))


def downgrade():
    op.drop_column("workloads", "placement_notes")
