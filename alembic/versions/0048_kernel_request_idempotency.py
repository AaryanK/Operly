"""add Kernel request idempotency claims

Revision ID: 0048_kernel_request_idempotency
Revises: 0047_operly_kernel_v3
"""

from alembic import op
import sqlalchemy as sa

revision = "0048_kernel_request_idempotency"
down_revision = "0047_operly_kernel_v3"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("kernel_request_claims"):
        return
    op.create_table(
        "kernel_request_claims",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=400), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("scope_kind", sa.String(length=20), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("principal_id", sa.String(length=160), nullable=True),
        sa.Column("capability_id", sa.String(length=160), nullable=True),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_kernel_request_claim_idempotency_key"),
    )
    for column in (
        "idempotency_key",
        "request_id",
        "scope_kind",
        "workspace_id",
        "owner_user_id",
        "principal_id",
        "capability_id",
        "status",
        "run_id",
        "created_at",
        "updated_at",
    ):
        op.create_index(
            f"ix_kernel_request_claims_{column}",
            "kernel_request_claims",
            [column],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("kernel_request_claims"):
        op.drop_table("kernel_request_claims")
