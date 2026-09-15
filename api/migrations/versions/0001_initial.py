"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-08

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slug", sa.String, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slug", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "slug", name="uq_site_org_slug"),
    )

    op.create_table(
        "provider_categories",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("description", sa.String, nullable=True),
    )

    op.create_table(
        "capabilities",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("category_id", sa.String, sa.ForeignKey("provider_categories.id"), nullable=False),
        sa.Column("description", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category_id", sa.String, sa.ForeignKey("provider_categories.id"), nullable=False),
        sa.Column("provider_type", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("instance_name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("contract_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("provider_version", sa.String, nullable=True),
        sa.Column("configuration", postgresql.JSONB, nullable=True),
        sa.Column("connection_health", sa.String, nullable=False, server_default="unknown"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "provider_capabilities",
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id"), primary_key=True),
        sa.Column("capability_id", sa.String, sa.ForeignKey("capabilities.id"), primary_key=True),
    )

    op.create_table(
        "capability_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("capability_id", sa.String, sa.ForeignKey("capabilities.id"), nullable=False),
        sa.Column("scope_type", sa.String, nullable=False, server_default="global"),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", sa.String, nullable=True),
    )

    op.create_table(
        "pve_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("hostname", sa.String, nullable=False),
        sa.Column("api_port", sa.Integer, nullable=False, server_default="8006"),
        sa.Column("tls_verify", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("tls_fingerprint", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "pve_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pve_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pve_targets.id"), nullable=False),
        sa.Column("slot_name", sa.String, nullable=False),
        sa.Column("token_user", sa.String, nullable=False),
        sa.Column("token_id", sa.String, nullable=False),
        sa.Column("encrypted_secret", sa.Text, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="untested"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String, nullable=True),
        sa.UniqueConstraint("pve_target_id", "slot_name", name="uq_credential_target_slot"),
    )

    op.create_table(
        "clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("pve_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pve_targets.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("quorate", sa.Boolean, nullable=True),
        sa.Column("pve_version", sa.String, nullable=True),
        sa.Column("node_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_missing", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="unknown"),
        sa.Column("cpu_usage_pct", sa.Float, nullable=True),
        sa.Column("mem_usage_pct", sa.Float, nullable=True),
        sa.Column("mem_total_bytes", sa.BigInteger, nullable=True),
        sa.Column("uptime_seconds", sa.BigInteger, nullable=True),
        sa.Column("pve_version", sa.String, nullable=True),
        sa.Column("kernel_version", sa.String, nullable=True),
        sa.Column("pending_updates", sa.Integer, nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_missing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("cluster_id", "name", name="uq_node_cluster_name"),
    )

    op.create_table(
        "workloads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("vmid", sa.Integer, nullable=False),
        sa.Column("name", sa.String, nullable=True),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="unknown"),
        sa.Column("cpu_cores", sa.Integer, nullable=True),
        sa.Column("memory_bytes", sa.BigInteger, nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=True),
        sa.Column("ha_state", sa.String, nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_missing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("cluster_id", "vmid", name="uq_workload_cluster_vmid"),
    )

    op.create_table(
        "storage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("type", sa.String, nullable=True),
        sa.Column("scope", sa.String, nullable=False),
        sa.Column("capacity_bytes", sa.BigInteger, nullable=True),
        sa.Column("used_bytes", sa.BigInteger, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="unknown"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_missing", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("site_id", "node_id", "cluster_id", "name", name="uq_storage_identity"),
    )

    op.create_table(
        "pve_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=True),
        sa.Column("upid", sa.String, nullable=False, unique=True),
        sa.Column("task_type", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=True),
        sa.Column("user", sa.String, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_status", sa.String, nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String, nullable=True),
        sa.Column("actor_type", sa.String, nullable=False, server_default="system"),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_category", sa.String, nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("operation", sa.String, nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state_before", postgresql.JSONB, nullable=True),
        sa.Column("state_after", postgresql.JSONB, nullable=True),
        sa.Column("result", sa.String, nullable=False, server_default="success"),
        sa.Column("severity", sa.String, nullable=False, server_default="info"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("rollback_classification", sa.String, nullable=True),
        sa.Column("justification", sa.Text, nullable=True),
        sa.Column("event_metadata", postgresql.JSONB, nullable=True),
    )
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_name", sa.String, nullable=False, server_default="Default Organization"),
        sa.Column("site_name", sa.String, nullable=False, server_default="Default Site"),
        sa.Column("inventory_refresh_interval_seconds", sa.Integer, nullable=False, server_default="300"),
        sa.Column("tls_verify_default", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String, nullable=False, server_default="UTC"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("app_settings")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("pve_tasks")
    op.drop_table("storage")
    op.drop_table("workloads")
    op.drop_table("nodes")
    op.drop_table("clusters")
    op.drop_table("pve_credentials")
    op.drop_table("pve_targets")
    op.drop_table("capability_grants")
    op.drop_table("provider_capabilities")
    op.drop_table("providers")
    op.drop_table("capabilities")
    op.drop_table("provider_categories")
    op.drop_table("sites")
    op.drop_table("organizations")
