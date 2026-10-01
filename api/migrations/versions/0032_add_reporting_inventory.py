"""Add the reporting inventory tables (per-workload config, disks, NICs,
snapshots, and collection-run history) behind the Platform -> Reporting page.

PVE only exposes this detail through one API call per VM, so it is
collected on its own schedule (pyxie_core.report_collection) rather than
inside discovery, and stored here so reports are instant and exportable.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _ts(name="collected_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    op.create_table(
        "workload_configs",
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True),
        _ts(),
        sa.Column("sockets", sa.Integer, nullable=True),
        sa.Column("cores_per_socket", sa.Integer, nullable=True),
        sa.Column("cpu_type", sa.String, nullable=True),
        sa.Column("memory_mb", sa.Integer, nullable=True),
        sa.Column("balloon_mb", sa.Integer, nullable=True),
        sa.Column("machine", sa.String, nullable=True),
        sa.Column("bios", sa.String, nullable=True),
        sa.Column("onboot", sa.Boolean, nullable=True),
        sa.Column("protection", sa.Boolean, nullable=True),
        sa.Column("agent_enabled", sa.Boolean, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("hostname", sa.String, nullable=True),
        sa.Column("guest_ips", JSONB, nullable=True),
        sa.Column("guest_ips_source", sa.String, nullable=True),
    )
    op.create_table(
        "workload_disks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot", sa.String, nullable=False),
        sa.Column("bus", sa.String, nullable=True),
        sa.Column("storage", sa.String, nullable=True),
        sa.Column("volume", sa.String, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("is_cdrom", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cache", sa.String, nullable=True),
        sa.Column("discard", sa.String, nullable=True),
        sa.Column("ssd", sa.Boolean, nullable=True),
        sa.Column("iothread", sa.Boolean, nullable=True),
        sa.Column("backup", sa.Boolean, nullable=True),
        _ts(),
        sa.UniqueConstraint("workload_id", "slot", name="uq_workload_disk_slot"),
    )
    op.create_table(
        "workload_nics_report",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=True),
        sa.Column("mac", sa.String, nullable=True),
        sa.Column("bridge", sa.String, nullable=True),
        sa.Column("vlan_tag", sa.Integer, nullable=True),
        sa.Column("firewall", sa.Boolean, nullable=True),
        sa.Column("rate_mbps", sa.Float, nullable=True),
        sa.Column("link_down", sa.Boolean, nullable=True),
        sa.Column("ip_config", sa.String, nullable=True),
        sa.Column("ips", JSONB, nullable=True),
        _ts(),
        sa.UniqueConstraint("workload_id", "slot", name="uq_workload_nic_report_slot"),
    )
    op.create_table(
        "workload_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("parent", sa.String, nullable=True),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("includes_ram", sa.Boolean, nullable=True),
        _ts(),
        sa.UniqueConstraint("workload_id", "name", name="uq_workload_snapshot_name"),
    )
    op.create_table(
        "report_collection_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _ts("started_at"),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("triggered_by", sa.String, nullable=False, server_default="schedule"),
        sa.Column("workloads_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("workloads_collected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("workloads_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_workload_disks_workload", "workload_disks", ["workload_id"])
    op.create_index("ix_workload_nics_report_workload", "workload_nics_report", ["workload_id"])
    op.create_index("ix_workload_snapshots_workload", "workload_snapshots", ["workload_id"])


def downgrade():
    op.drop_table("report_collection_runs")
    op.drop_table("workload_snapshots")
    op.drop_table("workload_nics_report")
    op.drop_table("workload_disks")
    op.drop_table("workload_configs")
