"""add account-owned connectors

Revision ID: 0033_personal_connectors
Revises: 0032_scope_boundaries
"""

from alembic import op
import sqlalchemy as sa

revision = "0033_personal_connectors"
down_revision = "0032_scope_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("account_connector_secrets"):
        op.create_table(
            "account_connector_secrets",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_account_connector_secrets_user_id", "account_connector_secrets", ["user_id"], unique=False)

    inspector = sa.inspect(bind)
    if not inspector.has_table("account_connectors"):
        op.create_table(
            "account_connectors",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("connector_type", sa.String(length=60), nullable=False),
            sa.Column("provider", sa.String(length=60), nullable=False),
            sa.Column("display_name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("credential_reference", sa.String(length=36), nullable=True),
            sa.Column("provider_account_id", sa.String(length=320), nullable=True),
            sa.Column("granted_scopes_json", sa.Text(), nullable=False),
            sa.Column("configuration_json", sa.Text(), nullable=False),
            sa.Column("health_status", sa.String(length=40), nullable=False),
            sa.Column("last_health_check", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["credential_reference"], ["account_connector_secrets.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "provider", "provider_account_id", name="uq_account_connector_account"),
        )
        op.create_index("ix_account_connectors_user_id", "account_connectors", ["user_id"], unique=False)
        op.create_index("ix_account_connectors_provider", "account_connectors", ["provider"], unique=False)
        op.create_index("ix_account_connectors_status", "account_connectors", ["status"], unique=False)
        op.create_index("ix_account_connectors_enabled", "account_connectors", ["enabled"], unique=False)
        op.create_index("ix_account_connector_user_provider", "account_connectors", ["user_id", "provider", "enabled", "status"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("account_connectors"):
        op.drop_index("ix_account_connector_user_provider", table_name="account_connectors")
        op.drop_index("ix_account_connectors_enabled", table_name="account_connectors")
        op.drop_index("ix_account_connectors_status", table_name="account_connectors")
        op.drop_index("ix_account_connectors_provider", table_name="account_connectors")
        op.drop_index("ix_account_connectors_user_id", table_name="account_connectors")
        op.drop_table("account_connectors")
    inspector = sa.inspect(bind)
    if inspector.has_table("account_connector_secrets"):
        op.drop_index("ix_account_connector_secrets_user_id", table_name="account_connector_secrets")
        op.drop_table("account_connector_secrets")
