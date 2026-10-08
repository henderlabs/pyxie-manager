"""Per-guest "do not move" flag: never moved by Balance Load or automatic balancing."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workloads", sa.Column("do_not_move", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("workloads", "do_not_move")
