"""Add operations.progress -- structured, periodically-updated migration
progress (transferred/total/rate/pct), parsed from the PVE task log during
the monitoring stage so the UI can show real movement instead of a static
'monitoring' badge.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("operations", sa.Column("progress", postgresql.JSONB, nullable=True))


def downgrade():
    op.drop_column("operations", "progress")
