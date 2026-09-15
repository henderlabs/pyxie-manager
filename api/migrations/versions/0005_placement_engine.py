"""DRS-style placement engine: workload sensitivity tag + affinity rules.
Node performance/trust tiers deliberately do NOT get new columns -- they
reuse the existing generic 'policies' table (scope_type='node',
scope_id=<node id>, key='placement.performance_tier'/'placement.trust_tier'),
same "narrower scope, data not enum" pattern as everything else policy-like
in this app.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("sensitivity", sa.String, nullable=False, server_default="standard"))

    op.create_table(
        "placement_affinity_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_type", sa.String, nullable=False),  # keep_together | keep_apart
        sa.Column("scope_type", sa.String, nullable=False),  # workload_pair | tag_group
        sa.Column("workload_ids", postgresql.JSONB, nullable=True),  # [uuid, uuid] for workload_pair
        sa.Column("tag", sa.String, nullable=True),  # for tag_group
        sa.Column("strict", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

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
                "id": "SAFE-TRUST-001",
                "title": "Workload sensitivity vs. node trust tier",
                "description": "A workload tagged 'restricted' may not be placed on a node tagged trust_tier=low.",
                "category": "placement",
            },
            {
                "id": "SAFE-AFFINITY-001",
                "title": "PyXie affinity/anti-affinity rule violation",
                "description": "Destination would violate a PyXie-level keep_together/keep_apart placement rule.",
                "category": "placement",
            },
        ],
    )


def downgrade():
    op.execute("DELETE FROM safety_rules WHERE id IN ('SAFE-TRUST-001','SAFE-AFFINITY-001')")
    op.drop_table("placement_affinity_rules")
    op.drop_column("workloads", "sensitivity")
