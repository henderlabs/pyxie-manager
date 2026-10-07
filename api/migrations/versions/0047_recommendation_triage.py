"""Acknowledge / dismiss bookkeeping on recommendations (same model as findings, migration 0044).

lifecycle_state keeps the states it already had. New: who/when for acknowledged and dismissed, and the fingerprint of
the suggestion at the moment it was triaged, so a dismissal ends when the suggestion itself changes (rightsizing: the
suggested size). Old 'snoozed' rows (the hidden 30-day dismiss) become dismissed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recommendations", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendations", sa.Column("acknowledged_by", sa.String, nullable=True))
    op.add_column("recommendations", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("recommendations", sa.Column("dismissed_by", sa.String, nullable=True))
    op.add_column("recommendations", sa.Column("triage_fingerprint", sa.String, nullable=True))
    op.execute("UPDATE recommendations SET lifecycle_state = 'dismissed' WHERE lifecycle_state = 'snoozed'")
    op.execute("UPDATE recommendations SET dismissed_at = updated_at WHERE lifecycle_state = 'dismissed'")
    op.execute("UPDATE recommendations SET acknowledged_at = updated_at WHERE lifecycle_state = 'acknowledged'")


def downgrade():
    for c in ("triage_fingerprint", "dismissed_by", "dismissed_at", "acknowledged_by", "acknowledged_at"):
        op.drop_column("recommendations", c)
