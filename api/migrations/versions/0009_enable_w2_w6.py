"""Enable operation_types for Stages W2-W6, now that their workflow modules
exist. host.update stays enabled for dry-run/preflight purposes only -- its
own dry_run_host_update() always blocks (SAFE-UPDATE-001), since PVE has no
API to actually apply updates yet (see host_update_workflow.py docstring).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

ENABLE_IDS = [
    "node.evacuate",
    "workload.shutdown",
    "workload.start",
    "workload.force_stop",
    "host.update",
    "host.reboot",
    "maintenance.run",
]


def upgrade():
    conn = op.get_bind()
    for op_id in ENABLE_IDS:
        conn.execute(sa.text("UPDATE operation_types SET enabled = true WHERE id = :id"), {"id": op_id})


def downgrade():
    conn = op.get_bind()
    for op_id in ENABLE_IDS:
        conn.execute(sa.text("UPDATE operation_types SET enabled = false WHERE id = :id"), {"id": op_id})
