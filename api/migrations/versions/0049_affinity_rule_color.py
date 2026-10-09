"""Optional per-rule display color for affinity rules (#rrggbb); NULL = the default palette color."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("placement_affinity_rules", sa.Column("color", sa.String, nullable=True))


def downgrade():
    op.drop_column("placement_affinity_rules", "color")
