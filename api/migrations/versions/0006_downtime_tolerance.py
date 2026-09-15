"""Workload downtime_tolerance tier -- low/standard/high, same vocabulary as
node performance_tier/trust_tier so they compose in placement scoring. A
'low'-tolerance (critical) workload gets a stronger pull toward high-tier
nodes and is hard-blocked from trust_tier=low destinations, same rule as a
'restricted'-sensitivity workload.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("downtime_tolerance", sa.String, nullable=False, server_default="standard"))

    safety_rules = sa.table(
        "safety_rules",
        sa.column("id", sa.String),
        sa.column("title", sa.String),
        sa.column("description", sa.Text),
        sa.column("category", sa.String),
    )
    op.bulk_insert(
        safety_rules,
        [
            {
                "id": "SAFE-DOWNTIME-001",
                "title": "Low-downtime-tolerance workload requires shutdown/offline handling",
                "description": "A workload tagged downtime_tolerance=low was classified as needing shutdown_in_place or offline migration for this maintenance action -- requires explicit human review, not a routine warning.",
                "category": "placement",
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM safety_rules WHERE id = 'SAFE-DOWNTIME-001'")
    op.drop_column("workloads", "downtime_tolerance")
