"""durable governed agent runtime state

Revision ID: 0057_agent_runtime_foundation
Revises: 0056_universal_workflow_scope_events
"""

from alembic import op
import sqlalchemy as sa

revision = "0057_agent_runtime_foundation"
down_revision = "0056_universal_workflow_scope_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("agent_runtime_runs"):
        op.create_table(
            "agent_runtime_runs",
            sa.Column("id", sa.String(120), primary_key=True),
            sa.Column("scope_kind", sa.String(20), nullable=False),
            sa.Column(
                "workspace_id",
                sa.String(36),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "owner_user_id",
                sa.String(36),
                sa.ForeignKey("app_users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "authority_user_id",
                sa.String(36),
                sa.ForeignKey("app_users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("principal_id", sa.String(200), nullable=False),
            sa.Column("conversation_id", sa.String(120), nullable=True),
            sa.Column("source_channel", sa.String(40), nullable=False),
            sa.Column("source_surface", sa.String(40), nullable=False),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("plan_json", sa.Text(), nullable=False),
            sa.Column("budget_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
            sa.Column("current_step_id", sa.String(120), nullable=True),
            sa.Column(
                "cancellation_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("lease_token", sa.String(80), nullable=True),
            sa.Column("lease_until", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "(scope_kind = 'workspace' AND workspace_id IS NOT NULL) OR "
                "(scope_kind = 'personal' AND workspace_id IS NULL AND owner_user_id IS NOT NULL)",
                name="ck_agent_runtime_run_scope_owner",
            ),
        )
        for column in (
            "scope_kind",
            "workspace_id",
            "owner_user_id",
            "authority_user_id",
            "principal_id",
            "conversation_id",
            "status",
            "current_step_id",
            "cancellation_requested",
            "lease_token",
            "lease_until",
            "created_at",
            "updated_at",
        ):
            op.create_index(f"ix_agent_runtime_runs_{column}", "agent_runtime_runs", [column])

    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_runtime_steps"):
        op.create_table(
            "agent_runtime_steps",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "agent_run_id",
                sa.String(120),
                sa.ForeignKey("agent_runtime_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("step_id", sa.String(120), nullable=False),
            sa.Column("step_order", sa.Integer(), nullable=False),
            sa.Column("capability_id", sa.String(200), nullable=False),
            sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("request_id", sa.String(160), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "kernel_run_id",
                sa.String(36),
                sa.ForeignKey("kernel_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "approval_id",
                sa.String(36),
                sa.ForeignKey("kernel_approvals.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "agent_run_id", "step_id", name="uq_agent_runtime_step_identity"
            ),
        )
        for column in (
            "agent_run_id",
            "step_id",
            "capability_id",
            "request_id",
            "status",
            "kernel_run_id",
            "approval_id",
            "created_at",
        ):
            op.create_index(f"ix_agent_runtime_steps_{column}", "agent_runtime_steps", [column])

    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_runtime_step_attempts"):
        op.create_table(
            "agent_runtime_step_attempts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "agent_run_id",
                sa.String(120),
                sa.ForeignKey("agent_runtime_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "agent_step_id",
                sa.String(36),
                sa.ForeignKey("agent_runtime_steps.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("capability_id", sa.String(200), nullable=False),
            sa.Column("request_id", sa.String(160), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column(
                "kernel_run_id",
                sa.String(36),
                sa.ForeignKey("kernel_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "approval_id",
                sa.String(36),
                sa.ForeignKey("kernel_approvals.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "agent_step_id", "attempt", name="uq_agent_runtime_step_attempt"
            ),
        )
        for column in (
            "agent_run_id",
            "agent_step_id",
            "capability_id",
            "request_id",
            "status",
            "kernel_run_id",
            "approval_id",
            "created_at",
        ):
            op.create_index(
                f"ix_agent_runtime_step_attempts_{column}",
                "agent_runtime_step_attempts",
                [column],
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_runtime_step_attempts"):
        op.drop_table("agent_runtime_step_attempts")
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_runtime_steps"):
        op.drop_table("agent_runtime_steps")
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_runtime_runs"):
        op.drop_table("agent_runtime_runs")
