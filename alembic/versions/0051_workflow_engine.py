"""add durable Workspace workflow engine

Revision ID: 0051_workflow_engine
Revises: 0050_workspace_agent_computer
"""

from alembic import op
import sqlalchemy as sa

revision = "0051_workflow_engine"
down_revision = "0050_workspace_agent_computer"
branch_labels = None
depends_on = None


def _index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes(table)} if inspector.has_table(table) else set()
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_definitions"):
        op.create_table(
            "workflow_definitions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="disabled"),
            sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    for name, cols in [
        ("ix_workflow_definitions_workspace_id", ["workspace_id"]),
        ("ix_workflow_definitions_owner_user_id", ["owner_user_id"]),
        ("ix_workflow_definitions_status", ["status"]),
        ("ix_workflow_definitions_created_at", ["created_at"]),
        ("ix_workflow_definitions_updated_at", ["updated_at"]),
    ]:
        _index(name, "workflow_definitions", cols)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_versions"):
        op.create_table(
            "workflow_versions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workflow_id", sa.String(length=36), sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("spec_json", sa.Text(), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_number"),
        )
    for name, cols in [
        ("ix_workflow_versions_workflow_id", ["workflow_id"]),
        ("ix_workflow_versions_created_by_user_id", ["created_by_user_id"]),
        ("ix_workflow_versions_created_at", ["created_at"]),
    ]:
        _index(name, "workflow_versions", cols)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_schedules"):
        op.create_table(
            "workflow_schedules",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workflow_id", sa.String(length=36), sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("schedule_type", sa.String(length=30), nullable=False),
            sa.Column("schedule_json", sa.Text(), nullable=False),
            sa.Column("timezone", sa.String(length=80), nullable=False, server_default="UTC"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_fired_at", sa.DateTime(), nullable=True),
            sa.Column("lease_token", sa.String(length=80), nullable=True),
            sa.Column("lease_until", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("workflow_id", name="uq_workflow_schedule_workflow"),
        )
    for name, cols in [
        ("ix_workflow_schedules_workflow_id", ["workflow_id"]),
        ("ix_workflow_schedules_schedule_type", ["schedule_type"]),
        ("ix_workflow_schedules_enabled", ["enabled"]),
        ("ix_workflow_schedules_next_run_at", ["next_run_at"]),
        ("ix_workflow_schedules_lease_token", ["lease_token"]),
        ("ix_workflow_schedules_lease_until", ["lease_until"]),
    ]:
        _index(name, "workflow_schedules", cols)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_runs"):
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workflow_id", sa.String(length=36), sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workflow_version_id", sa.String(length=36), sa.ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("initiated_by_user_id", sa.String(length=36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("trigger_type", sa.String(length=30), nullable=False, server_default="manual"),
            sa.Column("trigger_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("dedupe_key", sa.String(length=220), nullable=False, unique=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
            sa.Column("current_step_key", sa.String(length=80), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("lease_token", sa.String(length=80), nullable=True),
            sa.Column("lease_until", sa.DateTime(), nullable=True),
            sa.Column("scheduled_for", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
    for name, cols in [
        ("ix_workflow_runs_workflow_id", ["workflow_id"]),
        ("ix_workflow_runs_workflow_version_id", ["workflow_version_id"]),
        ("ix_workflow_runs_workspace_id", ["workspace_id"]),
        ("ix_workflow_runs_owner_user_id", ["owner_user_id"]),
        ("ix_workflow_runs_initiated_by_user_id", ["initiated_by_user_id"]),
        ("ix_workflow_runs_trigger_type", ["trigger_type"]),
        ("ix_workflow_runs_dedupe_key", ["dedupe_key"]),
        ("ix_workflow_runs_status", ["status"]),
        ("ix_workflow_runs_current_step_key", ["current_step_key"]),
        ("ix_workflow_runs_lease_token", ["lease_token"]),
        ("ix_workflow_runs_lease_until", ["lease_until"]),
        ("ix_workflow_runs_scheduled_for", ["scheduled_for"]),
        ("ix_workflow_runs_created_at", ["created_at"]),
        ("ix_workflow_runs_updated_at", ["updated_at"]),
    ]:
        _index(name, "workflow_runs", cols)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_step_runs"):
        op.create_table(
            "workflow_step_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workflow_run_id", sa.String(length=36), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_key", sa.String(length=80), nullable=False),
            sa.Column("step_order", sa.Integer(), nullable=False),
            sa.Column("step_kind", sa.String(length=30), nullable=False),
            sa.Column("capability_id", sa.String(length=160), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("request_id", sa.String(length=160), nullable=True),
            sa.Column("kernel_run_id", sa.String(length=36), nullable=True),
            sa.Column("approval_id", sa.String(length=36), nullable=True),
            sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("wait_until", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("workflow_run_id", "step_key", name="uq_workflow_step_run_key"),
        )
    for name, cols in [
        ("ix_workflow_step_runs_workflow_run_id", ["workflow_run_id"]),
        ("ix_workflow_step_runs_step_key", ["step_key"]),
        ("ix_workflow_step_runs_capability_id", ["capability_id"]),
        ("ix_workflow_step_runs_status", ["status"]),
        ("ix_workflow_step_runs_request_id", ["request_id"]),
        ("ix_workflow_step_runs_kernel_run_id", ["kernel_run_id"]),
        ("ix_workflow_step_runs_approval_id", ["approval_id"]),
        ("ix_workflow_step_runs_wait_until", ["wait_until"]),
        ("ix_workflow_step_runs_created_at", ["created_at"]),
    ]:
        _index(name, "workflow_step_runs", cols)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workflow_trace_events"):
        op.create_table(
            "workflow_trace_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("workspace_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workflow_id", sa.String(length=36), sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workflow_run_id", sa.String(length=36), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True),
            sa.Column("step_run_id", sa.String(length=36), sa.ForeignKey("workflow_step_runs.id", ondelete="CASCADE"), nullable=True),
            sa.Column("event_type", sa.String(length=160), nullable=False),
            sa.Column("actor_type", sa.String(length=40), nullable=False, server_default="system"),
            sa.Column("actor_id", sa.String(length=160), nullable=True),
            sa.Column("capability_id", sa.String(length=160), nullable=True),
            sa.Column("kernel_run_id", sa.String(length=36), nullable=True),
            sa.Column("approval_id", sa.String(length=36), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, cols in [
        ("ix_workflow_trace_events_workspace_id", ["workspace_id"]),
        ("ix_workflow_trace_events_workflow_id", ["workflow_id"]),
        ("ix_workflow_trace_events_workflow_run_id", ["workflow_run_id"]),
        ("ix_workflow_trace_events_step_run_id", ["step_run_id"]),
        ("ix_workflow_trace_events_event_type", ["event_type"]),
        ("ix_workflow_trace_events_actor_id", ["actor_id"]),
        ("ix_workflow_trace_events_capability_id", ["capability_id"]),
        ("ix_workflow_trace_events_kernel_run_id", ["kernel_run_id"]),
        ("ix_workflow_trace_events_approval_id", ["approval_id"]),
        ("ix_workflow_trace_events_created_at", ["created_at"]),
    ]:
        _index(name, "workflow_trace_events", cols)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in ["workflow_trace_events", "workflow_step_runs", "workflow_runs", "workflow_schedules", "workflow_versions", "workflow_definitions"]:
        if inspector.has_table(table):
            op.drop_table(table)
            inspector = sa.inspect(op.get_bind())
