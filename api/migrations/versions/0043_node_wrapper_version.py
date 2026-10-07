"""Remember each node's last seen host-wrapper version, so an outdated wrapper can raise a finding."""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("wrapper_version", sa.String(), nullable=True))
    op.add_column("nodes", sa.Column("wrapper_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "wrapper_checked_at")
    op.drop_column("nodes", "wrapper_version")
