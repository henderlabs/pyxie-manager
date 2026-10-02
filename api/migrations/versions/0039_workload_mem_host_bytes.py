"""Per-VM host-side memory size (bytes) -- the fallback PVE reports when a guest's
balloon stats briefly drop out.

For VMs that DO report guest memory, PVE's live figure alternates between the real
guest-used value and the host-side size of the QEMU process (PAW-JT-JohnLee: 24%
then 100.4%, every few seconds). Knowing the host-side size lets the live-memory
refresher recognise the fallback reading and hold the last good one instead of
flashing a false 100%.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("mem_host_bytes", sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column("workloads", "mem_host_bytes")
