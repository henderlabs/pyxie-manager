"""Add Node.management_ip, populated from PVE's own /cluster/status (each
node entry's 'ip' field -- its corosync ring0 address, which is the node's
own management IP in this deployment) during discovery. This is what lets
PyXie resolve a cluster-aware, failover-capable API endpoint instead of
always talking to the single hardcoded PveTarget.hostname -- see
credentials.resolve_pve_endpoint().

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nodes", sa.Column("management_ip", sa.String(), nullable=True))


def downgrade():
    op.drop_column("nodes", "management_ip")
