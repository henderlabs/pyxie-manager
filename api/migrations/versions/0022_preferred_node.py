"""Sticky per-VM preferred HOST (node) to live on -- a soft placement
preference, not a hard pin. Null means no preference (balance/tier scoring
alone decides, as today); an explicit value gives that node a strong
scoring bonus in recommend_destinations() so it wins whenever it's
otherwise eligible, but every real hard block (headroom, CPU compat,
passthrough, trust tier, PyXie affinity) still overrides it unchanged.

Deliberately its own column on workloads, not a placement_affinity_rule
(those express workload<->workload/tag relationships, not workload<->node)
and not a node tier (those classify a node in general, they don't let a
workload point at one).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("preferred_node_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_workloads_preferred_node_id_nodes", "workloads", "nodes", ["preferred_node_id"], ["id"],
    )


def downgrade():
    op.drop_constraint("fk_workloads_preferred_node_id_nodes", "workloads", type_="foreignkey")
    op.drop_column("workloads", "preferred_node_id")
