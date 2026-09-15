"""Partial unique index on resource_locks(resource_type, resource_id)
WHERE released_at IS NULL -- makes it structurally impossible for two
un-released locks to coexist on the same resource, closing a real
SELECT-then-INSERT race in locks.acquire_lock(): if a lock expires
mid-run or two workers race between query and insert, PyXie could fight
itself. A single-worker deployment only reduces the probability of that
race; it does not make the SELECT-then-INSERT pattern correct on its own.

acquire_lock() now proactively releases any expired-but-never-released
lock on a resource before checking/inserting, so this constraint doesn't
need to know about expires_at at all -- released_at IS NULL becomes an
honest signal on its own.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-14

"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_resource_locks_active_resource"


def upgrade():
    op.execute(
        f"CREATE UNIQUE INDEX {INDEX_NAME} ON resource_locks (resource_type, resource_id) "
        f"WHERE released_at IS NULL"
    )


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
