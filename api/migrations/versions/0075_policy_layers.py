"""Backup policies get copy layers (aux copies) and a garbage-collection schedule.

Revision ID: 0075
Revises: 0074
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("backup_policies", sa.Column("layers", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("backup_policies", sa.Column("gc_schedule", sa.String, nullable=True))


def downgrade():
    op.drop_column("backup_policies", "gc_schedule")
    op.drop_column("backup_policies", "layers")
