"""drop app_settings duplicate org/site name columns

Revision ID: 9d0911c0473d
Revises: 0024
Create Date: 2026-09-14 18:32:11.194220

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d0911c0473d'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("app_settings", "organization_name")
    op.drop_column("app_settings", "site_name")


def downgrade():
    op.add_column(
        "app_settings",
        sa.Column("organization_name", sa.String(), nullable=False, server_default="Default Organization"),
    )
    op.add_column(
        "app_settings",
        sa.Column("site_name", sa.String(), nullable=False, server_default="Default Site"),
    )
