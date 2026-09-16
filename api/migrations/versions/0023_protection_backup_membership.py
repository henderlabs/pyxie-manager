"""New operation_type: protection.backup_membership -- add/remove a VM from
a PBS-backed vzdump job, or switch it to PVE's native all-guests mode, from
the Protection page. Built after a real gap was found live: a newly
created VM can be silently missing from a backup job's explicit vmid
list, because that job never joins an explicit list on its own when it
isn't already in all-guests mode.

Same Safety Contract as every other PVE write (dry-run -> approve ->
revalidate -> lock -> execute -> verify -> audit), but PUT /cluster/backup
is synchronous -- no PVE task/UPID -- so there is no monitoring stage,
same shape as workload.resize's plain config-PUT reconfigure step.

Also backfills the 'protection.jobs.membership.write' capability: inserts
the Capability row itself (idempotent -- api/app/seed.py's own startup
seed would otherwise be the one to create it, but this migration may run
before that seed step on a fresh deploy) and grants it to every PBS
provider that already exists, so this doesn't silently apply only to PBS
targets created after today.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-13

"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

CAPABILITY_ID = "protection.jobs.membership.write"


def upgrade():
    operation_types = sa.table(
        "operation_types",
        sa.column("id", sa.String),
        sa.column("category", sa.String),
        sa.column("description", sa.Text),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_rollback_classification", sa.String),
        sa.column("required_credential_slot", sa.String),
        sa.column("requires_individual_approval", sa.Boolean),
        sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(
        operation_types,
        [
            {
                "id": "protection.backup_membership",
                "category": "protection",
                "description": "Add/remove a VM from a PBS-backed vzdump backup job, or switch it to all-guests mode.",
                "stages": ["dry_run", "awaiting_approval", "revalidating", "executing", "verifying", "audit"],
                "default_rollback_classification": "manual_reversible",
                "required_credential_slot": "maintenance",
                "requires_individual_approval": True,
                "enabled": True,
            },
        ],
    )

    conn = op.get_bind()

    # The capabilities insert below has a FK to provider_categories.id='protection'.
    # That category is normally seeded by api/app/seed.py's own seed_defaults()
    # at app startup -- but on a genuinely fresh deploy (initial `alembic upgrade
    # head` before the app has ever run once), this migration executes first and
    # that row doesn't exist yet, so the capabilities insert below fails its FK
    # constraint. Idempotent, matches PROVIDER_CATEGORIES in
    # shared/pyxie_core/seed_data.py -- seed_defaults() re-running later is a
    # harmless no-op against an already-present row.
    conn.execute(
        sa.text(
            "INSERT INTO provider_categories (id, description) "
            "VALUES ('protection', 'Backup and data protection') ON CONFLICT (id) DO NOTHING"
        )
    )

    conn.execute(
        sa.text(
            "INSERT INTO capabilities (id, category_id, description, created_at) "
            "VALUES (:id, 'protection', :description, now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": CAPABILITY_ID, "description": "Add/remove VMs from a PBS-backed vzdump job, or switch it to all-guests mode"},
    )

    pbs_provider_ids = [
        row[0]
        for row in conn.execute(sa.text("SELECT id FROM providers WHERE provider_type = 'pbs' AND instance_name != 'unconfigured'"))
    ]
    for provider_id in pbs_provider_ids:
        conn.execute(
            sa.text(
                "INSERT INTO provider_capabilities (provider_id, capability_id) VALUES (:provider_id, :capability_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"provider_id": provider_id, "capability_id": CAPABILITY_ID},
        )
        already_granted = conn.execute(
            sa.text(
                "SELECT 1 FROM capability_grants WHERE provider_id = :provider_id AND capability_id = :capability_id"
            ),
            {"provider_id": provider_id, "capability_id": CAPABILITY_ID},
        ).first()
        if not already_granted:
            conn.execute(
                sa.text(
                    "INSERT INTO capability_grants (id, provider_id, capability_id, scope_type, granted_by, granted_at) "
                    "VALUES (:id, :provider_id, :capability_id, 'global', 'migration_0023_backfill', now())"
                ),
                {"id": str(uuid.uuid4()), "provider_id": provider_id, "capability_id": CAPABILITY_ID},
            )


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM capability_grants WHERE capability_id = :id"), {"id": CAPABILITY_ID})
    conn.execute(sa.text("DELETE FROM provider_capabilities WHERE capability_id = :id"), {"id": CAPABILITY_ID})
    conn.execute(sa.text("DELETE FROM capabilities WHERE id = :id"), {"id": CAPABILITY_ID})
    op.execute("DELETE FROM operation_types WHERE id = 'protection.backup_membership'")
