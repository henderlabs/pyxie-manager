"""Record which API endpoint each PVE target is actually using.

Discovery now fails over between cluster members; the chosen endpoint and any
unreachable members are stored on the target so the UI and findings can show
them. Additive, nullable.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pve_targets", sa.Column("endpoint_status", JSONB, nullable=True))


def downgrade():
    op.drop_column("pve_targets", "endpoint_status")
