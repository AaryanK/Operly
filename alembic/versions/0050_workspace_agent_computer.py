"""add governed Workspace Agent Computer sessions

Revision ID: 0050_workspace_agent_computer
Revises: 0049_kernel_approvals
"""

from alembic import op
import sqlalchemy as sa

revision = "0050_workspace_agent_computer"
down_revision = "0049_kernel_approvals"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("agent_computer_sessions"):
        op.create_table(
            "agent_computer_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("app_users.id"), nullable=False),
            sa.Column("principal_id", sa.String(length=200), nullable=False),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False, server_default=""),
            sa.Column("action", sa.String(length=60), nullable=False),
            sa.Column("state", sa.String(length=40), nullable=False, server_default="ready"),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("software_projects.id"), nullable=True),
            sa.Column("solution_id", sa.String(length=36), sa.ForeignKey("solutions.id"), nullable=True),
            sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("current_capability_id", sa.String(length=200), nullable=True),
            sa.Column("current_request_id", sa.String(length=160), nullable=True),
            sa.Column("current_run_id", sa.String(length=80), nullable=True),
            sa.Column("approval_id", sa.String(length=80), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_agent_computer_sessions_tenant_id", "agent_computer_sessions", ["tenant_id"])
        op.create_index("ix_agent_computer_sessions_user_id", "agent_computer_sessions", ["user_id"])
        op.create_index("ix_agent_computer_sessions_principal_id", "agent_computer_sessions", ["principal_id"])
        op.create_index("ix_agent_computer_sessions_action", "agent_computer_sessions", ["action"])
        op.create_index("ix_agent_computer_sessions_state", "agent_computer_sessions", ["state"])
        op.create_index("ix_agent_computer_sessions_project_id", "agent_computer_sessions", ["project_id"])
        op.create_index("ix_agent_computer_sessions_solution_id", "agent_computer_sessions", ["solution_id"])
        op.create_index("ix_agent_computer_sessions_current_run_id", "agent_computer_sessions", ["current_run_id"])
        op.create_index("ix_agent_computer_sessions_approval_id", "agent_computer_sessions", ["approval_id"])
        op.create_index("ix_agent_computer_sessions_updated_at", "agent_computer_sessions", ["updated_at"])
        op.create_index(
            "ix_agent_computer_session_scope_updated",
            "agent_computer_sessions",
            ["tenant_id", "user_id", "updated_at"],
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("agent_computer_steps"):
        op.create_table(
            "agent_computer_steps",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column(
                "session_id",
                sa.String(length=36),
                sa.ForeignKey("agent_computer_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="recorded"),
            sa.Column("capability_id", sa.String(length=200), nullable=True),
            sa.Column("request_id", sa.String(length=160), nullable=True),
            sa.Column("run_id", sa.String(length=80), nullable=True),
            sa.Column("approval_id", sa.String(length=80), nullable=True),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("session_id", "sequence", name="uq_agent_computer_step_sequence"),
        )
        op.create_index("ix_agent_computer_steps_tenant_id", "agent_computer_steps", ["tenant_id"])
        op.create_index("ix_agent_computer_steps_session_id", "agent_computer_steps", ["session_id"])
        op.create_index("ix_agent_computer_steps_created_at", "agent_computer_steps", ["created_at"])
        op.create_index(
            "ix_agent_computer_step_scope",
            "agent_computer_steps",
            ["tenant_id", "session_id", "sequence"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("agent_computer_steps"):
        op.drop_table("agent_computer_steps")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("agent_computer_sessions"):
        op.drop_table("agent_computer_sessions")
