"""principals, client grants, and workspace tool exposure

Revision ID: 0024_principals_clients_mcp
Revises: 0023_workspace_rbac
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_principals_clients_mcp"
down_revision = "0023_workspace_rbac"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "principals" not in tables:
        op.create_table(
            "principals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("kind", sa.String(30), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("claimed_by_user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for col in ("kind", "user_id", "status", "expires_at", "claimed_by_user_id"):
            op.create_index(f"ix_principals_{col}", "principals", [col])
        op.create_index("ix_principal_kind_status", "principals", ["kind", "status"])

    if "external_principal_bindings" not in tables:
        op.create_table(
            "external_principal_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("principal_id", sa.String(36), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("provider_subject", sa.String(255), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("verified", sa.Boolean(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "provider_subject", name="uq_external_principal_provider_subject"),
        )
        op.create_index("ix_external_principal_bindings_principal_id", "external_principal_bindings", ["principal_id"])
        op.create_index("ix_external_principal_provider", "external_principal_bindings", ["provider", "principal_id"])

    if "client_grants" not in tables:
        op.create_table(
            "client_grants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("principal_id", sa.String(36), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("client_id", sa.String(120), nullable=False),
            sa.Column("scopes_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("principal_id", "tenant_id", "client_id", name="uq_client_grant_scope"),
        )
        for col in ("principal_id", "tenant_id", "client_id", "status", "expires_at"):
            op.create_index(f"ix_client_grants_{col}", "client_grants", [col])

    if "workspace_tool_exposures" not in tables:
        op.create_table(
            "workspace_tool_exposures",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tool_id", sa.String(160), nullable=False),
            sa.Column("surface", sa.String(40), nullable=False),
            sa.Column("exposed", sa.Boolean(), nullable=False),
            sa.Column("access_mode", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("tenant_id", "tool_id", "surface", name="uq_workspace_tool_surface"),
        )
        op.create_index("ix_workspace_tool_exposures_tenant_id", "workspace_tool_exposures", ["tenant_id"])
        op.create_index("ix_workspace_tool_exposure", "workspace_tool_exposures", ["tenant_id", "surface", "exposed"])


def downgrade():
    op.drop_table("workspace_tool_exposures")
    op.drop_table("client_grants")
    op.drop_table("external_principal_bindings")
    op.drop_table("principals")
