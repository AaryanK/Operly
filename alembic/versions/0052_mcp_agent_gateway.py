"""add MCP agent gateway authorization codes

Revision ID: 0052_mcp_agent_gateway
Revises: 0051_workflow_engine
"""

from alembic import op
import sqlalchemy as sa

revision = "0052_mcp_agent_gateway"
down_revision = "0051_workflow_engine"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("mcp_authorization_codes"):
        return
    op.create_table(
        "mcp_authorization_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("grant_id", sa.String(36), sa.ForeignKey("client_grants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_id", sa.String(36), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(120), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mcp_authorization_codes_code_hash", "mcp_authorization_codes", ["code_hash"], unique=True)
    for column in ("grant_id", "principal_id", "tenant_id", "client_id", "expires_at", "consumed_at"):
        op.create_index(f"ix_mcp_authorization_codes_{column}", "mcp_authorization_codes", [column])
    op.create_index(
        "ix_mcp_authorization_code_grant_expiry",
        "mcp_authorization_codes",
        ["grant_id", "expires_at"],
    )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("mcp_authorization_codes"):
        op.drop_table("mcp_authorization_codes")
