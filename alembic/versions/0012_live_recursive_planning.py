"""durable live recursive planning
Revision ID: 0012_live_recursive_planning
Revises: 0011_recursive_planning
"""
from alembic import op
import sqlalchemy as sa

revision="0012_live_recursive_planning"
down_revision="0011_recursive_planning"
branch_labels=None
depends_on=None

def upgrade():
    existing=set(sa.inspect(op.get_bind()).get_table_names())
    if "planning_model_invocations" not in existing:
      op.create_table("planning_model_invocations",
        sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("plan_id",sa.String(36),sa.ForeignKey("software_plans.id",ondelete="CASCADE"),nullable=False),
        sa.Column("plan_version",sa.Integer,nullable=False),sa.Column("node_id",sa.String(160)),sa.Column("role",sa.String(40),nullable=False),sa.Column("planning_mode",sa.String(30),nullable=False),sa.Column("provider",sa.String(80),nullable=False),sa.Column("model_id",sa.String(160),nullable=False),
        sa.Column("request_id",sa.String(160),nullable=False),sa.Column("context_digest",sa.String(64),nullable=False),sa.Column("prompt_version",sa.String(40),nullable=False,server_default="v1"),sa.Column("attempt",sa.Integer,nullable=False,server_default="1"),
        sa.Column("structured_output_json",sa.Text,nullable=False,server_default="{}"),sa.Column("raw_response",sa.Text),sa.Column("validation_errors_json",sa.Text,nullable=False,server_default="[]"),sa.Column("retry_history_json",sa.Text,nullable=False,server_default="[]"),
        sa.Column("latency_ms",sa.Integer,nullable=False,server_default="0"),sa.Column("input_tokens",sa.Integer,nullable=False,server_default="0"),sa.Column("output_tokens",sa.Integer,nullable=False,server_default="0"),sa.Column("failure_classification",sa.String(80)),
        sa.Column("parent_invocation_id",sa.String(36),sa.ForeignKey("planning_model_invocations.id")),sa.Column("retry_of_id",sa.String(36),sa.ForeignKey("planning_model_invocations.id")),sa.Column("created_at",sa.DateTime,nullable=False))
      for name in ("tenant_id","plan_id","plan_version","node_id","role","planning_mode","request_id","context_digest","failure_classification"):
        op.create_index(f"ix_planning_model_invocations_{name}","planning_model_invocations",[name])
    if "planning_work_items" not in existing:
      op.create_table("planning_work_items",
        sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("plan_id",sa.String(36),sa.ForeignKey("software_plans.id",ondelete="CASCADE"),nullable=False),
        sa.Column("plan_version",sa.Integer,nullable=False),sa.Column("node_id",sa.String(160)),sa.Column("work_type",sa.String(40),nullable=False),sa.Column("priority",sa.Integer,nullable=False,server_default="100"),sa.Column("state",sa.String(30),nullable=False,server_default="queued"),
        sa.Column("attempts",sa.Integer,nullable=False,server_default="0"),sa.Column("payload_json",sa.Text,nullable=False,server_default="{}"),sa.Column("findings_json",sa.Text,nullable=False,server_default="[]"),sa.Column("blocked_question",sa.Text),
        sa.Column("available_at",sa.DateTime,nullable=False),sa.Column("created_at",sa.DateTime,nullable=False),sa.Column("updated_at",sa.DateTime,nullable=False),sa.UniqueConstraint("plan_id","plan_version","node_id","work_type",name="uq_planning_work_item"))
      for name in ("tenant_id","plan_id","node_id","work_type","priority","state","available_at"):
        op.create_index(f"ix_planning_work_items_{name}","planning_work_items",[name])

def downgrade():
    raise RuntimeError("Live planning audit downgrade is unsafe; restore a verified backup")
