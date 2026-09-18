"""Add notification_rules table.

Adjustable email triggers and recipients (Settings > Notifications).
Seeds one default rule reproducing the behavior that existed before
rules did: email critical findings and their recoveries, all categories,
to the recipient already saved in Settings.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-18

"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "notification_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("categories", JSONB, nullable=False, server_default="[]"),
        sa.Column("min_severity", sa.String, nullable=False, server_default="critical"),
        sa.Column("send_recovery", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("recipients", JSONB, nullable=False, server_default="[]"),
        sa.Column("include_admins", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    existing = op.get_bind().execute(sa.text("SELECT notification_recipient FROM app_settings WHERE id = 1")).scalar()
    recipients = [a.strip() for a in (existing or "").split(",") if a.strip()]
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "name": "Critical alerts",
                "enabled": True,
                "categories": [],
                "min_severity": "critical",
                "send_recovery": True,
                "recipients": recipients,
                "include_admins": False,
            }
        ],
    )


def downgrade():
    op.drop_table("notification_rules")
