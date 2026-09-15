"""Sticky per-VM storage preference ('local' | 'shared' | null) for
migration/placement destination selection. Null means infer from wherever
the workload's disk lives today; an explicit value overrides that.

Fixes a real bug found in production use: recommend_storage_for_candidate()
was actively recommending relocation TO shared storage for any workload
not already there, the opposite of this deployment's actual storage
preference -- different environments reasonably default to different
answers here (local-first vs. shared-first), which is exactly why this
is a per-workload preference rather than a hardcoded assumption.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("storage_preference", sa.String(), nullable=True))


def downgrade():
    op.drop_column("workloads", "storage_preference")
