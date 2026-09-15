"""Configurable rightsizing peak-utilization targets -- fixes a real gap
found live: several VMs downsized to 90-101% memory, one pegged at 100%
CPU. The old peak-safety multiplier (1.1x observed max) left almost no
margin, sizing a VM so its historical peak became ~91% of the new
allocation with zero tolerance for measurement noise or growth. Replaced
with an explicit "target peak utilization %" per resource, defaulting to
75% for both, adjustable here rather than buried in code -- memory can
run stricter than CPU since a tight memory VM pages/OOMs while a tight
CPU VM just schedules slower, and the operator sets the actual numbers.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-12

"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_settings",
        sa.Column("rightsizing_cpu_peak_target_pct", sa.Integer(), nullable=False, server_default="75"),
    )
    op.add_column(
        "app_settings",
        sa.Column("rightsizing_mem_peak_target_pct", sa.Integer(), nullable=False, server_default="75"),
    )


def downgrade():
    op.drop_column("app_settings", "rightsizing_mem_peak_target_pct")
    op.drop_column("app_settings", "rightsizing_cpu_peak_target_pct")
