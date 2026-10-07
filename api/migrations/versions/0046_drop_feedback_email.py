"""Feedback email is no longer an admin setting: the in-app "Send by email" opens the user's own mail app, addressed
to the PyXie developers, so nothing is stored per server."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("app_settings", "feedback_email")


def downgrade():
    op.add_column("app_settings", sa.Column("feedback_email", sa.String, nullable=True))
