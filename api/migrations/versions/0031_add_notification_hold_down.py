"""Add flap hold-down state for notifications.

A host that keeps dropping in and out (e.g. a node losing quorum
repeatedly) would otherwise produce an alert plus a recovery notice on
every flip. Findings now remember when their current state began
(state_since) and whether the operator was last told about it being
active (notified_active); a state change is announced only once it has
held for app_settings.notification_hold_down_minutes.

Existing findings are backfilled as already-announced so upgrading does
not re-alert on long-standing conditions.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("findings", sa.Column("state_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("findings", sa.Column("notified_active", sa.Boolean, nullable=True))
    op.execute("UPDATE findings SET state_since = COALESCE(resolved_at, first_observed), notified_active = active")
    op.add_column(
        "app_settings",
        sa.Column("notification_hold_down_minutes", sa.Integer, nullable=False, server_default="5"),
    )


def downgrade():
    op.drop_column("app_settings", "notification_hold_down_minutes")
    op.drop_column("findings", "notified_active")
    op.drop_column("findings", "state_since")
