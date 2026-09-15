"""Phase 1-4 read-only product: auth, metrics, findings, recommendations,
policies, safety rules, protection subsystem, maintenance planner,
notifications, internal job log, provider validation status.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("providers", sa.Column("implementation_status", sa.String, nullable=False, server_default="not_implemented"))
    op.add_column("providers", sa.Column("live_validation_status", sa.String, nullable=False, server_default="not_tested"))

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("display_name", sa.String, nullable=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String, nullable=True),
        sa.Column("user_agent", sa.String, nullable=True),
    )

    op.create_table(
        "metric_points",
        sa.Column("object_type", sa.String, primary_key=True),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("metric", sa.String, primary_key=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("source", sa.String, nullable=False, server_default="pve_rrd"),
    )
    op.create_index("ix_metric_points_lookup", "metric_points", ["object_type", "object_id", "metric", "sampled_at"])

    op.create_table(
        "findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("object_type", sa.String, nullable=True),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=True),
        sa.Column("dedupe_key", sa.String, nullable=False),
        sa.Column("first_observed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String, nullable=False, server_default="system"),
        sa.Column("confidence", sa.String, nullable=True),
        sa.Column("finding_metadata", postgresql.JSONB, nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_finding_dedupe_key"),
    )
    op.create_index("ix_findings_active", "findings", ["active"])

    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String, nullable=False, server_default="organization"),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_id", "key", name="uq_policy_scope_key"),
    )

    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("object_type", sa.String, nullable=True),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=True),
        sa.Column("expected_benefit", sa.Text, nullable=True),
        sa.Column("possible_impact", sa.Text, nullable=True),
        sa.Column("severity", sa.String, nullable=False, server_default="info"),
        sa.Column("risk", sa.String, nullable=False, server_default="low"),
        sa.Column("confidence", sa.String, nullable=True),
        sa.Column("observation_window_days", sa.Integer, nullable=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("policies.id"), nullable=True),
        sa.Column("dedupe_key", sa.String, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lifecycle_state", sa.String, nullable=False, server_default="open"),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_recommendation_dedupe_key"),
    )
    op.create_index("ix_recommendations_lifecycle", "recommendations", ["lifecycle_state"])

    op.create_table(
        "safety_rules",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String, nullable=False),
    )

    op.create_table(
        "protection_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("hostname", sa.String, nullable=False),
        sa.Column("api_port", sa.Integer, nullable=False, server_default="8007"),
        sa.Column("tls_verify", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "protection_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("protection_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("protection_targets.id"), nullable=False),
        sa.Column("slot_name", sa.String, nullable=False, server_default="inventory"),
        sa.Column("token_user", sa.String, nullable=False),
        sa.Column("token_id", sa.String, nullable=False),
        sa.Column("encrypted_secret", sa.Text, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="untested"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("protection_target_id", "slot_name", name="uq_protection_cred_target_slot"),
    )

    op.create_table(
        "protection_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workload_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workloads.id"), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("protected", sa.String, nullable=False, server_default="unknown"),
        sa.Column("last_successful_job_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_restore_point_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_operation", sa.Boolean, nullable=True),
        sa.Column("next_known_operation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_defined", sa.Boolean, nullable=True),
        sa.Column("sla_compliant", sa.String, nullable=False, server_default="unknown"),
        sa.Column("restore_verified", sa.String, nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.String, nullable=True),
        sa.Column("known_limitations", sa.Text, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workload_id", "provider_id", name="uq_protection_result_workload_provider"),
    )

    op.create_table(
        "maintenance_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inventory_state_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("protection_state_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metric_state_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="current"),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        sa.Column("blocking_safety_rules", postgresql.JSONB, nullable=True),
        sa.Column("created_by", sa.String, nullable=True),
    )

    op.create_table(
        "maintenance_plan_workloads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("maintenance_plans.id"), nullable=False),
        sa.Column("workload_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workloads.id"), nullable=False),
        sa.Column("classification", sa.String, nullable=False),
        sa.Column("reasons", postgresql.JSONB, nullable=True),
        sa.Column("blocking_safety_rules", postgresql.JSONB, nullable=True),
    )

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("severity", sa.String, nullable=False, server_default="informational"),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("object_type", sa.String, nullable=True),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="unread"),
        sa.Column("source", sa.String, nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "internal_job_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_name", sa.String, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="running"),
        sa.Column("result_summary", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )


def downgrade():
    op.drop_table("internal_job_runs")
    op.drop_table("notifications")
    op.drop_table("maintenance_plan_workloads")
    op.drop_table("maintenance_plans")
    op.drop_table("protection_results")
    op.drop_table("protection_credentials")
    op.drop_table("protection_targets")
    op.drop_table("safety_rules")
    op.drop_index("ix_recommendations_lifecycle", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_table("policies")
    op.drop_index("ix_findings_active", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_metric_points_lookup", table_name="metric_points")
    op.drop_table("metric_points")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_column("providers", "live_validation_status")
    op.drop_column("providers", "implementation_status")
