"""Latest QEMU-responsiveness result per running VM, written by the worker's liveness loop.

PVE reports a VM as running from its process alone; a VM whose QEMU control socket has hung
(VM 115 on m503, dead ~2 days) still looks fine and only shows up when a live migration
hangs. The worker probes every running VM about once a minute and upserts one row here.
Findings raise "VM is not responding" once `consecutive_bad` reaches 2; the API reads these
rows instead of probing on every page view. Its own table, so it never contends with the
long inventory sync.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workload_liveness",
        sa.Column("workload_id", UUID(as_uuid=True), sa.ForeignKey("workloads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("detail", sa.String, nullable=True),
        sa.Column("elapsed", sa.Float, nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bad_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_bad", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_table("workload_liveness")
