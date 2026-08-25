"""add first-party platform analytics events

Revision ID: 0040_platform_analytics
Revises: 0039_scope_aware_actions
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_platform_analytics"
down_revision = "0039_scope_aware_actions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("product_analytics_events"):
        return

    op.create_table(
        "product_analytics_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("event_name", sa.String(length=60), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["auth_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_analytics_events_user_id", "product_analytics_events", ["user_id"], unique=False)
    op.create_index("ix_product_analytics_events_tenant_id", "product_analytics_events", ["tenant_id"], unique=False)
    op.create_index("ix_product_analytics_events_session_id", "product_analytics_events", ["session_id"], unique=False)
    op.create_index("ix_product_analytics_events_event_name", "product_analytics_events", ["event_name"], unique=False)
    op.create_index("ix_product_analytics_events_country_code", "product_analytics_events", ["country_code"], unique=False)
    op.create_index("ix_product_analytics_events_created_at", "product_analytics_events", ["created_at"], unique=False)
    op.create_index("ix_product_analytics_event_created", "product_analytics_events", ["event_name", "created_at"], unique=False)
    op.create_index("ix_product_analytics_country_created", "product_analytics_events", ["country_code", "created_at"], unique=False)
    op.create_index("ix_product_analytics_user_created", "product_analytics_events", ["user_id", "created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("product_analytics_events"):
        return
    op.drop_table("product_analytics_events")
