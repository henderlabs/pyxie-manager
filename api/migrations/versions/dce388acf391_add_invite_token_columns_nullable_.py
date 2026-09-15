"""add invite token columns, nullable password hash

Revision ID: dce388acf391
Revises: 9d0911c0473d
Create Date: 2026-09-14 20:34:41.913074

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dce388acf391'
down_revision = '9d0911c0473d'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=True)
    op.add_column("users", sa.Column("invite_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("invite_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_invite_token", "users", ["invite_token"])
    op.alter_column("users", "is_admin", server_default=sa.false())


def downgrade():
    op.alter_column("users", "is_admin", server_default=sa.true())
    op.drop_constraint("uq_users_invite_token", "users", type_="unique")
    op.drop_column("users", "invite_token_expires_at")
    op.drop_column("users", "invite_token")
    op.alter_column("users", "password_hash", existing_type=sa.String(), nullable=False)
