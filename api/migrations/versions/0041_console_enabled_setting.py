"""Console kill switch: app_settings.console_enabled.

The embedded VM console (PyXie proxies PVE's noVNC websocket) is off by default, like
pve_mutations_enabled, and is re-read on every ticket request and every websocket connect.
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("console_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "console_enabled")
