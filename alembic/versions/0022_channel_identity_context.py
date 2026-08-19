"""channel identities and scoped context

Revision ID: 0022_channel_identity_context
Revises: 0021_auth_security
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_channel_identity_context"
down_revision = "0021_auth_security"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "external_identities" not in tables:
        op.create_table(
            "external_identities",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("provider_subject", sa.String(255), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "provider_subject", name="uq_external_identity_provider_subject"),
        )
        op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
        op.create_index("ix_external_identity_user_provider", "external_identities", ["user_id", "provider"])

    if "identity_link_challenges" not in tables:
        op.create_table(
            "identity_link_challenges",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("mode", sa.String(30), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("external_user_id", sa.String(255), nullable=True),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("secret_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("code_hash", sa.String(64), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for column in ("provider", "mode", "user_id", "external_user_id", "secret_hash", "code_hash", "expires_at", "consumed_at"):
            op.create_index(
                f"ix_identity_link_challenges_{column}",
                "identity_link_challenges",
                [column],
                unique=column == "secret_hash",
            )

    if "channel_installations" not in tables:
        op.create_table(
            "channel_installations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("external_space_id", sa.String(255), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=False),
            sa.Column("provisional", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "external_space_id", name="uq_channel_installation_provider_space"),
        )
        op.create_index("ix_channel_installations_tenant_id", "channel_installations", ["tenant_id"])
        op.create_index("ix_channel_installations_provisional", "channel_installations", ["provisional"])
        op.create_index("ix_channel_installations_status", "channel_installations", ["status"])
        op.create_index("ix_channel_installation_tenant_provider", "channel_installations", ["tenant_id", "provider"])

    if "channel_conversation_states" not in tables:
        op.create_table(
            "channel_conversation_states",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("external_user_id", sa.String(255), nullable=False),
            sa.Column("external_conversation_id", sa.String(255), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("active_tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
            sa.Column("agent_conversation_id", sa.String(120), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "external_user_id", "external_conversation_id", name="uq_channel_conversation_actor"),
        )
        op.create_index("ix_channel_conversation_states_user_id", "channel_conversation_states", ["user_id"])
        op.create_index("ix_channel_conversation_states_active_tenant_id", "channel_conversation_states", ["active_tenant_id"])

    if "context_records" not in tables:
        op.create_table(
            "context_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("scope_type", sa.String(30), nullable=False),
            sa.Column("visibility", sa.String(20), nullable=False),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("conversation_id", sa.String(120), nullable=True),
            sa.Column("channel_provider", sa.String(40), nullable=True),
            sa.Column("channel_space_id", sa.String(255), nullable=True),
            sa.Column("source_message_id", sa.String(255), nullable=True),
            sa.Column("kind", sa.String(50), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for column in ("scope_type", "visibility", "tenant_id", "owner_user_id", "conversation_id"):
            op.create_index(f"ix_context_records_{column}", "context_records", [column])
        op.create_index("ix_context_tenant_scope_created", "context_records", ["tenant_id", "scope_type", "created_at"])
        op.create_index("ix_context_owner_scope_created", "context_records", ["owner_user_id", "scope_type", "created_at"])
        op.create_index("ix_context_conversation_created", "context_records", ["conversation_id", "created_at"])


def downgrade():
    op.drop_table("context_records")
    op.drop_table("channel_conversation_states")
    op.drop_table("channel_installations")
    op.drop_table("identity_link_challenges")
    op.drop_table("external_identities")
