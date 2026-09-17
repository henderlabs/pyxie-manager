"""Add cluster_log_entries table.

PVE's own /cluster/log endpoint -- a syslog-style rolling feed merged
across every node in the cluster (daemon restarts, corosync/quorum
events, hardware/storage issues) -- was never ingested anywhere in
PyXie. Distinct from pve_tasks (a job's outcome): this is ambient
system activity. PVE itself only keeps a small rolling buffer, so this
table persists what's been seen on each discovery pass to build real
history, deduped on (cluster_id, pve_id) where pve_id is PVE's own
stable id for that log line.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cluster_log_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("pve_id", sa.String(), nullable=False),
        sa.Column("node", sa.String(), nullable=True),
        sa.Column("tag", sa.String(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cluster_id", "pve_id", name="uq_cluster_log_cluster_pve_id"),
    )
    op.create_index("ix_cluster_log_entries_cluster_id_logged_at", "cluster_log_entries", ["cluster_id", "logged_at"])


def downgrade():
    op.drop_index("ix_cluster_log_entries_cluster_id_logged_at", table_name="cluster_log_entries")
    op.drop_table("cluster_log_entries")
