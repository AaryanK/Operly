"""unified personal/workspace agent chat history

Revision ID: 0058_agent_chat_history
Revises: 0057_agent_runtime_foundation
"""

from alembic import op
import sqlalchemy as sa

revision = "0058_agent_chat_history"
down_revision = "0057_agent_runtime_foundation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_chat_conversations"):
        op.create_table(
            "agent_chat_conversations",
            sa.Column("id", sa.String(120), primary_key=True),
            sa.Column("scope_kind", sa.String(20), nullable=False),
            sa.Column("workspace_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("authority_user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("principal_id", sa.String(200), nullable=False),
            sa.Column("channel", sa.String(40), nullable=False),
            sa.Column("surface", sa.String(40), nullable=False),
            sa.Column("title", sa.String(250), nullable=False, server_default="Operly conversation"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "(scope_kind = 'workspace' AND workspace_id IS NOT NULL AND owner_user_id IS NULL) OR "
                "(scope_kind = 'personal' AND workspace_id IS NULL AND owner_user_id IS NOT NULL)",
                name="ck_agent_chat_conversation_scope_owner",
            ),
        )
        for column in ("scope_kind", "workspace_id", "owner_user_id", "authority_user_id", "principal_id", "channel", "surface"):
            op.create_index(f"ix_agent_chat_conversations_{column}", "agent_chat_conversations", [column])
        op.create_index(
            "ix_agent_chat_conversation_scope_principal_updated",
            "agent_chat_conversations",
            ["scope_kind", "workspace_id", "owner_user_id", "principal_id", "updated_at"],
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_chat_messages"):
        op.create_table(
            "agent_chat_messages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "conversation_id",
                sa.String(120),
                sa.ForeignKey("agent_chat_conversations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_agent_chat_messages_conversation_id", "agent_chat_messages", ["conversation_id"])
        op.create_index(
            "ix_agent_chat_message_conversation_created",
            "agent_chat_messages",
            ["conversation_id", "created_at"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_chat_messages"):
        op.drop_table("agent_chat_messages")
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_chat_conversations"):
        op.drop_table("agent_chat_conversations")
