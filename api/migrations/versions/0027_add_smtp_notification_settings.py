"""Add SMTP email-notification settings to app_settings.

PyXie had no email-sending capability at all before this -- no SMTP
library, no notification recipient, nothing. Adds the fields needed for
a Settings-page "Email notifications" card: an enable switch, the usual
SMTP connection fields, and a saved notification-recipient address that
both the "send test email" check and future real alert emails use.

The password is stored encrypted (smtp_encrypted_password), same
Fernet-based convention as every other credential in this app
(shared/pyxie_core/crypto.py) -- never plaintext, never returned to the
frontend.

Defaults to disabled/empty, same conservative posture as the
pve_mutations_enabled kill switch: a fresh column starts off, not
silently active.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_settings",
        sa.Column("smtp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("app_settings", sa.Column("smtp_host", sa.String(), nullable=True))
    op.add_column(
        "app_settings",
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
    )
    op.add_column("app_settings", sa.Column("smtp_username", sa.String(), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_encrypted_password", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("smtp_from_address", sa.String(), nullable=True))
    op.add_column(
        "app_settings",
        sa.Column("smtp_use_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("app_settings", sa.Column("notification_recipient", sa.String(), nullable=True))


def downgrade():
    op.drop_column("app_settings", "notification_recipient")
    op.drop_column("app_settings", "smtp_use_tls")
    op.drop_column("app_settings", "smtp_from_address")
    op.drop_column("app_settings", "smtp_encrypted_password")
    op.drop_column("app_settings", "smtp_username")
    op.drop_column("app_settings", "smtp_port")
    op.drop_column("app_settings", "smtp_host")
    op.drop_column("app_settings", "smtp_enabled")
