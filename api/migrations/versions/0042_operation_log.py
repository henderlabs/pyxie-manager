"""Live operation log: one row per appended chunk (host apt output or workflow stage line).

The Maintenance page tails these while a host update or reboot runs and keeps them afterwards.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("operation_id", UUID(as_uuid=True), sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.String(), nullable=False, server_default="host"),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_operation_log_op_id", "operation_log", ["operation_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_operation_log_op_id", table_name="operation_log")
    op.drop_table("operation_log")
