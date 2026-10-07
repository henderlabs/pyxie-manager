"""In-app feedback: the maintainer email address (optional) and a local record of what this instance has sent.

Feedback itself goes to GitHub (a prefilled issue the reporter reviews and submits) or by email through the
instance's own SMTP; this table only remembers that it happened, never the diagnostics or description.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("feedback_email", sa.String, nullable=True))
    op.create_table(
        "feedback_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("submitted_by", sa.String, nullable=True),
        sa.Column("diagnostics_included", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


def downgrade():
    op.drop_table("feedback_submissions")
    op.drop_column("app_settings", "feedback_email")
