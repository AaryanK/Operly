"""company operating system substrate

Revision ID: 0013_company_operating_system
Revises: 0012_live_recursive_planning
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_company_operating_system"
down_revision = "0012_live_recursive_planning"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "business_events" not in existing:
      op.create_table("business_events",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False), sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_type", sa.String(40), nullable=False), sa.Column("actor_id", sa.String(120)), sa.Column("source", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False), sa.Column("correlation_id", sa.String(36)), sa.Column("causation_id", sa.String(36)),
        sa.Column("metadata_json", sa.Text(), nullable=False))
      op.create_index("ix_business_events_tenant_id", "business_events", ["tenant_id"])
      op.create_index("ix_business_events_event_type", "business_events", ["event_type"])
      op.create_index("ix_business_events_occurred_at", "business_events", ["occurred_at"])
      op.create_index("ix_business_events_correlation_id", "business_events", ["correlation_id"])
      op.create_index("ix_business_events_tenant_type_time", "business_events", ["tenant_id", "event_type", "occurred_at"])
    if "business_actions" not in existing:
      op.create_table("business_actions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False), sa.Column("capability", sa.String(100), nullable=False), sa.Column("provider", sa.String(100)),
        sa.Column("arguments_json", sa.Text(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False), sa.Column("expected_outcome", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(30), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("policy_decision", sa.String(30)),
        sa.Column("approval_id", sa.String(36), sa.ForeignKey("approvals.id")), sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("verification_json", sa.Text(), nullable=False), sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
      for name in ("tenant_id", "capability", "status", "approval_id", "correlation_id"):
          op.create_index(f"ix_business_actions_{name}", "business_actions", [name])


def downgrade():
    op.drop_table("business_actions")
    op.drop_table("business_events")
