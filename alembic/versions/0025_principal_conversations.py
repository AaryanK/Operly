"""principal conversation persistence

Revision ID: 0025_principal_conversations
Revises: 0024_principals_clients_mcp
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_principal_conversations"
down_revision = "0024_principals_clients_mcp"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "principal_conversations" not in tables:
        op.create_table(
            "principal_conversations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("principal_id", sa.String(36), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(40), nullable=False),
            sa.Column("external_conversation_id", sa.String(255), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("principal_id", "provider", "external_conversation_id", name="uq_principal_conversation_origin"),
        )
        op.create_index("ix_principal_conversations_principal_id", "principal_conversations", ["principal_id"])
        op.create_index("ix_principal_conversations_status", "principal_conversations", ["status"])

    if "principal_messages" not in tables:
        op.create_table(
            "principal_messages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("conversation_id", sa.String(36), sa.ForeignKey("principal_conversations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(30), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_principal_messages_conversation_id", "principal_messages", ["conversation_id"])


def downgrade():
    op.drop_table("principal_messages")
    op.drop_table("principal_conversations")
