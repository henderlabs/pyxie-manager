"""Move the PVE_MUTATIONS_ENABLED kill switch from an env var to a
Settings-page toggle backed by app_settings.

Previously this was PVE_MUTATIONS_ENABLED in .env, read fresh on every
write call (shared/pyxie_core/pve_write_client.py's _mutations_enabled())
but only ever changeable by editing the file and restarting pyxie-api/
pyxie-worker. Phil: it should live on the Settings page with a clear
description, not require a redeploy to toggle. The env var read is
removed from the write-path check entirely -- app_settings.id=1 is now
the sole source of truth, still re-checked fresh on every single write
call (not cached), same as before.

Defaults to false regardless of what any existing deployment's env var
currently says -- a fresh column starting conservative, not silently
inheriting an env value the operator may not have consciously chosen
for THIS control surface.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-16

"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "app_settings",
        sa.Column("pve_mutations_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("app_settings", "pve_mutations_enabled")
