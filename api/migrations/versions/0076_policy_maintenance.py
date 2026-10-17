"""Backup policies get maintenance switches: pruning, garbage collection and verification.

Revision ID: 0076
Revises: 0075
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("backup_policies", sa.Column("maintenance", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column("backup_policies", "maintenance")
