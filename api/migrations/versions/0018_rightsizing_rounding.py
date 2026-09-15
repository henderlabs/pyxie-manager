"""Rightsizing rounding conventions: memory suggestions were rounding to
the nearest 512MB, producing needlessly precise numbers like 8.5GB/9.5GB
-- switched to whole GB in rightsizing.py, no setting needed since
there's no real reason to keep half-GB granularity. vCPU suggestions stay
exact by default (single-socket hardware doesn't have the usual
NUMA-locality argument for even-only/power-of-2 core counts, and forcing
round-ups would waste allocation on smaller hosts) -- but added as an
explicit, off-by-default toggle for whenever that judgment call should
change (different hardware, a fleet-wide convention the operator wants
regardless).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_settings",
        sa.Column("rightsizing_round_vcpu_even", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("app_settings", "rightsizing_round_vcpu_even")
