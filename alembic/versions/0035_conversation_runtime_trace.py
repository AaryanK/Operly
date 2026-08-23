"""add conversation-scoped model runtime traces

Revision ID: 0035_conversation_runtime_trace
Revises: 0034_provider_trace_ids
"""

from alembic import op
import sqlalchemy as sa

revision = "0035_conversation_runtime_trace"
down_revision = "0034_provider_trace_ids"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("model_runtime_traces"):
        return
    op.create_table(
        "model_runtime_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("principal_id", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("surface", sa.String(length=80), nullable=True),
        sa.Column("component", sa.String(length=120), nullable=True),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_model_id", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("classification", sa.String(length=80), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_runtime_traces_run_id", "model_runtime_traces", ["run_id"], unique=False)
    op.create_index("ix_model_runtime_traces_conversation_id", "model_runtime_traces", ["conversation_id"], unique=False)
    op.create_index("ix_model_runtime_traces_tenant_id", "model_runtime_traces", ["tenant_id"], unique=False)
    op.create_index("ix_model_runtime_traces_user_id", "model_runtime_traces", ["user_id"], unique=False)
    op.create_index("ix_model_runtime_traces_attempt_id", "model_runtime_traces", ["attempt_id"], unique=False)
    op.create_index(
        "ix_model_runtime_trace_conversation_created",
        "model_runtime_traces",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_model_runtime_trace_run_created",
        "model_runtime_traces",
        ["run_id", "created_at"],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("model_runtime_traces"):
        return
    op.drop_index("ix_model_runtime_trace_run_created", table_name="model_runtime_traces")
    op.drop_index("ix_model_runtime_trace_conversation_created", table_name="model_runtime_traces")
    op.drop_index("ix_model_runtime_traces_attempt_id", table_name="model_runtime_traces")
    op.drop_index("ix_model_runtime_traces_user_id", table_name="model_runtime_traces")
    op.drop_index("ix_model_runtime_traces_tenant_id", table_name="model_runtime_traces")
    op.drop_index("ix_model_runtime_traces_conversation_id", table_name="model_runtime_traces")
    op.drop_index("ix_model_runtime_traces_run_id", table_name="model_runtime_traces")
    op.drop_table("model_runtime_traces")
