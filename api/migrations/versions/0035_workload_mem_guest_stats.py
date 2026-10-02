"""Per-VM flag: does PVE report guest-used memory for this VM?

PVE's memory figure for a VM is the guest-reported "used" memory only when the
balloon device / guest agent supplies stats; otherwise it silently falls back
to the host-side size of the QEMU process, which for a VM that has touched its
RAM is ~100% of its allocation regardless of what the guest is doing. PyXie
was presenting that fallback as real usage (meters pinned at 100%, and
rightsizing suggesting more memory for healthy VMs).

mem_guest_stats: true  = PVE reported guest-used memory (differs from the host
                         figure) at some point in the last hour of samples
                 false = every recent sample equals the host-side figure, so
                         there is no guest memory reading to show or assess
                 null  = not evaluated yet / not applicable (containers,
                         stopped VMs)
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("mem_guest_stats", sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column("workloads", "mem_guest_stats")
