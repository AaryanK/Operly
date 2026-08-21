"""workspace-defined roles and permissions

Revision ID: 0023_workspace_rbac
Revises: 0022_channel_identity_context
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_workspace_rbac"
down_revision = "0022_channel_identity_context"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "workspace_roles" not in tables:
        op.create_table(
            "workspace_roles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("key", sa.String(30), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("is_system", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("tenant_id", "key", name="uq_workspace_role_tenant_key"),
        )
        op.create_index("ix_workspace_roles_tenant_id", "workspace_roles", ["tenant_id"])
        op.create_index("ix_workspace_role_tenant_name", "workspace_roles", ["tenant_id", "name"])

    if "workspace_role_permissions" not in tables:
        op.create_table(
            "workspace_role_permissions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("role_id", sa.String(36), sa.ForeignKey("workspace_roles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("permission", sa.String(120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("role_id", "permission", name="uq_workspace_role_permission"),
        )
        op.create_index("ix_workspace_role_permissions_role_id", "workspace_role_permissions", ["role_id"])


def downgrade():
    op.drop_table("workspace_role_permissions")
    op.drop_table("workspace_roles")
