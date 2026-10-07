"""Acknowledge / dismiss state on findings.

Acknowledged: still listed, muted, left out of attention counts. Dismissed: hidden from the active view. Both end when
a new occurrence of the finding begins (it resolved and came back) or when its severity gets worse; findings._reconcile
clears these columns then.
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("findings", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("findings", sa.Column("acknowledged_by", sa.String, nullable=True))
    op.add_column("findings", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("findings", sa.Column("dismissed_by", sa.String, nullable=True))


def downgrade():
    for c in ("dismissed_by", "dismissed_at", "acknowledged_by", "acknowledged_at"):
        op.drop_column("findings", c)
