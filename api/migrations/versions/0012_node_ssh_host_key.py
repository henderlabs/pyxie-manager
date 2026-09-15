"""Pinned SSH host key per node, for Stage W4 host-maintenance SSH strict
host-key validation -- never blindly trust an unknown/changed host key,
per Chat's security review. Set only via an explicit operator-confirmed
pin action, never auto-added on first connect.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("ssh_host_key_type", sa.String(), nullable=True))
    op.add_column("nodes", sa.Column("ssh_host_key_base64", sa.Text(), nullable=True))
    op.add_column("nodes", sa.Column("ssh_host_key_fingerprint", sa.String(), nullable=True))
    op.add_column("nodes", sa.Column("ssh_host_key_pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("nodes", sa.Column("ssh_host_key_pinned_by", sa.String(), nullable=True))


def downgrade():
    op.drop_column("nodes", "ssh_host_key_pinned_by")
    op.drop_column("nodes", "ssh_host_key_pinned_at")
    op.drop_column("nodes", "ssh_host_key_fingerprint")
    op.drop_column("nodes", "ssh_host_key_base64")
    op.drop_column("nodes", "ssh_host_key_type")
