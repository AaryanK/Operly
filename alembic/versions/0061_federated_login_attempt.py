"""Add one-time OIDC authorization attempts without switching Operly auth.

Revision ID: 0061_federated_login_attempt
Revises: 0060_federated_identity_map
"""
from alembic import op
import sqlalchemy as sa

revision = "0061_federated_login_attempt"
down_revision = "0060_federated_identity_map"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "federated_login_attempts",
        sa.Column("state_hash", sa.String(length=64), primary_key=True),
        sa.Column("binding_hash", sa.String(length=64), nullable=False),
        sa.Column("verifier", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_federated_login_attempts_expires_at",
                    "federated_login_attempts", ["expires_at"])


def downgrade():
    op.drop_index("ix_federated_login_attempts_expires_at",
                  table_name="federated_login_attempts")
    op.drop_table("federated_login_attempts")
