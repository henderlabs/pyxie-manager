"""Per-VM guest memory-pressure samples (swap-in/out and page-fault counters).

PVE reports a VM's "used" memory with the guest's file cache counted as used, so
a healthy database or Kubernetes node reads 98% full. Rightsizing was telling the
operator to add memory to 71 such VMs. What separates "full of cache" from "short
of RAM" is whether the guest is actually swapping, which PVE exposes only as live
cumulative counters (no history), so PyXie now samples them every ~5 minutes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workload_mem_pressure",
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("swapped_in_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("swapped_out_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("major_faults", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("free_bytes", sa.BigInteger, nullable=True),
        sa.Column("total_bytes", sa.BigInteger, nullable=True),
    )
    op.create_index("ix_workload_mem_pressure_sampled_at", "workload_mem_pressure", ["sampled_at"])


def downgrade():
    op.drop_index("ix_workload_mem_pressure_sampled_at", table_name="workload_mem_pressure")
    op.drop_table("workload_mem_pressure")
