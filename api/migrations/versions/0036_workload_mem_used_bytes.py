"""Live per-VM memory used (bytes), as PVE reports it right now.

PyXie's RAM meter used to read the last/median history sample, which could
disagree with the PVE summary screen and the Datacenter table. Those read the
live `mem` / `maxmem` from PVE's VM list -- the same list inventory discovery
already fetches every minute -- so keep that number and show it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("mem_used_bytes", sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column("workloads", "mem_used_bytes")
