"""server-managed authentication and account recovery

Revision ID: 0021_auth_security
Revises: 0020_presence_operations
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_auth_security"
down_revision = "0020_presence_operations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    user_columns = {column["name"]: column for column in inspector.get_columns("app_users")}

    if not user_columns["password_hash"]["nullable"]:
        with op.batch_alter_table("app_users") as batch:
            batch.alter_column("password_hash", existing_type=sa.Text(), nullable=True)
    if "email_verified_at" not in user_columns:
        op.add_column("app_users", sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    if "updated_at" not in user_columns:
        op.add_column(
            "app_users",
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("'1970-01-01 00:00:00'"),
            ),
        )

    op.execute(
        "UPDATE app_users SET email_verified_at = created_at, updated_at = created_at "
        "WHERE email_verified_at IS NULL OR updated_at = '1970-01-01 00:00:00'"
    )

    if "auth_identities" not in tables:
        op.create_table(
            "auth_identities",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(30), nullable=False),
            sa.Column("provider_subject", sa.String(255), nullable=False),
            sa.Column("provider_email", sa.String(320), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("provider", "provider_subject", name="uq_auth_identity_subject"),
            sa.UniqueConstraint("user_id", "provider", name="uq_auth_identity_user_provider"),
        )
        op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])
    op.execute(
        "INSERT INTO auth_identities "
        "(id, user_id, provider, provider_subject, provider_email, created_at, updated_at) "
        "SELECT u.id, u.id, 'password', u.email, u.email, u.created_at, u.created_at "
        "FROM app_users u WHERE u.password_hash IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM auth_identities i WHERE i.user_id = u.id AND i.provider = 'password')"
    )

    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("csrf_token_hash", sa.String(64), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("last_activity_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("authenticated_at", sa.DateTime(), nullable=False),
            sa.Column("user_agent", sa.String(255), nullable=True),
            sa.Column("ip_hash", sa.String(64), nullable=True),
        )
        for column in ("token_hash", "user_id", "tenant_id", "expires_at", "revoked_at"):
            op.create_index(f"ix_auth_sessions_{column}", "auth_sessions", [column], unique=column == "token_hash")

    if "auth_challenges" not in tables:
        op.create_table(
            "auth_challenges",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("purpose", sa.String(40), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("target_email", sa.String(320), nullable=False),
            sa.Column("secret_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("code_hash", sa.String(64), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("delivery_status", sa.String(30), nullable=False),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for column in ("purpose", "user_id", "secret_hash", "expires_at", "consumed_at"):
            op.create_index(f"ix_auth_challenges_{column}", "auth_challenges", [column], unique=column == "secret_hash")

    if "auth_rate_limit_events" not in tables:
        op.create_table(
            "auth_rate_limit_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("endpoint", sa.String(50), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_auth_rate_limit_endpoint_key_created",
            "auth_rate_limit_events",
            ["endpoint", "key_hash", "created_at"],
        )
        op.create_index(
            "ix_auth_rate_limit_events_created_at",
            "auth_rate_limit_events",
            ["created_at"],
        )

    if "security_events" not in tables:
        op.create_table(
            "security_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
            sa.Column("event_type", sa.String(60), nullable=False),
            sa.Column("outcome", sa.String(20), nullable=False),
            sa.Column("ip_hash", sa.String(64), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for column in ("user_id", "tenant_id", "event_type", "created_at"):
            op.create_index(f"ix_security_events_{column}", "security_events", [column])


def downgrade():
    bind = op.get_bind()
    passwordless_users = bind.execute(
        sa.text("SELECT COUNT(*) FROM app_users WHERE password_hash IS NULL")
    ).scalar_one()
    if passwordless_users:
        raise RuntimeError(
            "Cannot downgrade authentication schema while passwordless accounts exist"
        )
    op.drop_table("security_events")
    op.drop_table("auth_rate_limit_events")
    op.drop_table("auth_challenges")
    op.drop_table("auth_sessions")
    op.drop_table("auth_identities")
    with op.batch_alter_table("app_users") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("email_verified_at")
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=False)
