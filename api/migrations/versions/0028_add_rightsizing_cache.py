"""Add rightsizing_cache table.

GET /api/rightsizing recomputed assess_all_workloads() live on every
request -- even after batching its queries (4.4s -> 1.1s for 130
workloads, verified byte-identical against the old per-workload
implementation), that's still real work paid on every page load that
touches rightsizing data. This table caches the last computed result
(one singleton row, same id=1 pattern as app_settings), populated by
the worker's existing ~5-minute run_all cycle and by a new manual
recompute trigger, so reads become an instant table lookup instead of
a live computation.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rightsizing_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessments", JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("rightsizing_cache")
