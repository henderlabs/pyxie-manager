"""Live per-VM memory, refreshed every ~30s independently of the 5-minute sync.

The RAM meter must match what PVE's summary screen shows right now. It used to be
refreshed only by the full inventory sync (every inventory_refresh_interval_seconds,
5 minutes by default, and it takes over a minute to run), so it could lag PVE by
minutes. A separate light worker loop upserts one row per running VM from PVE's
cluster resources list. Its own table, so it never contends for row locks with the
long sync transaction.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workload_live_mem",
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mem_used_bytes", sa.BigInteger, nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("workload_live_mem")
