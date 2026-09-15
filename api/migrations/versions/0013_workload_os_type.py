"""Workload OS type, synced from PVE's own qemu config 'ostype' field.
Rightsizing needs this to apply OS-aware minimum floors (e.g. a Windows
guest should never be suggested below 2 vCPU / 4GB regardless of observed
usage) -- P95-based sizing alone starves anything that's idle most of the
time and needs real resources when actually used.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("os_type", sa.String(), nullable=True))


def downgrade():
    op.drop_column("workloads", "os_type")
