"""Operations.dismissed -- lets a user clear a terminal (completed/failed/
blocked/cancelled) operation out of the default Recent Migration Operations
view without deleting it. Nothing is ever hard-deleted in this app; this is
the same "hide, don't destroy" pattern as Finding.active/resolved_at.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("operations", sa.Column("dismissed", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("operations", "dismissed")
