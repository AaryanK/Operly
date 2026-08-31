"""add durable Kernel approvals

Revision ID: 0049_kernel_approvals
Revises: 0048_kernel_request_idempotency
"""

from alembic import op
import sqlalchemy as sa

revision = "0049_kernel_approvals"
down_revision = "0048_kernel_request_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("kernel_approvals"):
        return
    op.create_table(
        "kernel_approvals",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("scope_kind", sa.String(length=20), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("requested_by_principal_id", sa.String(length=160), nullable=True),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("capability_id", sa.String(length=160), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("conversation_id", sa.String(length=160), nullable=True),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_run_id", sa.String(length=36), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["app_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["app_users.id"], ondelete="SET NULL"),
    )
    for column in (
        "scope_kind",
        "workspace_id",
        "owner_user_id",
        "requested_by_principal_id",
        "requested_by_user_id",
        "capability_id",
        "arguments_hash",
        "request_id",
        "conversation_id",
        "source_run_id",
        "status",
        "decided_by_user_id",
        "decided_at",
        "consumed_run_id",
        "consumed_at",
        "created_at",
    ):
        op.create_index(f"ix_kernel_approvals_{column}", "kernel_approvals", [column])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("kernel_approvals"):
        op.drop_table("kernel_approvals")
