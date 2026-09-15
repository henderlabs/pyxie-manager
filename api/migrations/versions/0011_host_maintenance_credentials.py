"""Stage W4: host_maintenance_credentials table for the SSH-based host
package-management identity -- deliberately separate from pve_credentials
(PVE API tokens). See models.HostMaintenanceCredential docstring.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "host_maintenance_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pve_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pve_targets.id"), nullable=False, unique=True),
        sa.Column("ssh_username", sa.String(), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("encrypted_private_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="untested"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_wrapper_version", sa.String(), nullable=True),
        sa.Column("last_seen_contract_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
    )


def downgrade():
    op.drop_table("host_maintenance_credentials")
