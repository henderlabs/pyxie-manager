"""vm.live_migrate gains an "offline" transport option (shutdown -> migrate
-> power back on) alongside the existing live/online path -- a node-local
disk being live-migrated has to block-mirror WHILE the guest keeps writing
to it, which is markedly slower than copying it once with the guest
stopped -- live migration can be too costly time-wise with node-local
storage. Adds "shutting_down"/"starting_up" to this
operation type's stages, matching workload.resize's own naming exactly --
same as that type, a "live" (or already-stopped) instance simply never
visits them, same accepted pattern already in place there.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

OLD_STAGES = ["dry_run", "awaiting_approval", "revalidating", "executing", "monitoring", "verifying", "audit"]
NEW_STAGES = [
    "dry_run", "awaiting_approval", "revalidating", "shutting_down",
    "executing", "monitoring", "starting_up", "verifying", "audit",
]


def _set_stages(stages: list[str]):
    operation_types = sa.table(
        "operation_types", sa.column("id", sa.String), sa.column("stages", postgresql.JSONB),
    )
    op.execute(
        operation_types.update().where(operation_types.c.id == "vm.live_migrate").values(stages=stages)
    )


def upgrade():
    _set_stages(NEW_STAGES)


def downgrade():
    _set_stages(OLD_STAGES)
