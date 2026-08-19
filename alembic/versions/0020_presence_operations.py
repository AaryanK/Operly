"""continuous presence operations
Revision ID: 0020_presence_operations
Revises: 0019_solution_production
"""
from alembic import op
import sqlalchemy as sa
revision="0020_presence_operations";down_revision="0019_solution_production";branch_labels=None;depends_on=None
def upgrade():
 tables=set(sa.inspect(op.get_bind()).get_table_names())
 if "presence_observations" not in tables:
  op.create_table("presence_observations",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("solution_id",sa.String(36),sa.ForeignKey("solutions.id"),nullable=False),sa.Column("observation_type",sa.String(60),nullable=False),sa.Column("status",sa.String(30),nullable=False),sa.Column("fingerprint",sa.String(64),nullable=False),sa.Column("evidence_json",sa.Text(),nullable=False),sa.Column("observed_version_reference",sa.String(120)),sa.Column("observed_at",sa.DateTime(),nullable=False),sa.Column("resolved_at",sa.DateTime()),sa.UniqueConstraint("tenant_id","solution_id","observation_type","fingerprint",name="uq_presence_observation_fingerprint"))
  for col in ("tenant_id","solution_id","observation_type","status","observed_at"):op.create_index(f"ix_presence_observations_{col}","presence_observations",[col])
 if "solution_improvement_proposals" not in tables:
  op.create_table("solution_improvement_proposals",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("solution_id",sa.String(36),sa.ForeignKey("solutions.id"),nullable=False),sa.Column("observation_id",sa.String(36),sa.ForeignKey("presence_observations.id"),nullable=False),sa.Column("action_id",sa.String(36),sa.ForeignKey("business_actions.id")),sa.Column("status",sa.String(30),nullable=False),sa.Column("issue",sa.Text(),nullable=False),sa.Column("supporting_evidence_json",sa.Text(),nullable=False),sa.Column("affected_facts_json",sa.Text(),nullable=False),sa.Column("affected_artifacts_json",sa.Text(),nullable=False),sa.Column("proposed_change_json",sa.Text(),nullable=False),sa.Column("expected_outcome",sa.Text(),nullable=False),sa.Column("risk",sa.String(30),nullable=False),sa.Column("approval_required",sa.Boolean(),nullable=False),sa.Column("before_version_reference",sa.String(120)),sa.Column("after_version_reference",sa.String(120)),sa.Column("deployment_id",sa.String(36),sa.ForeignKey("solution_deployments.id")),sa.Column("approved_by",sa.String(120)),sa.Column("verification_json",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False))
  for col in ("tenant_id","solution_id","observation_id","action_id","status"):op.create_index(f"ix_solution_improvement_proposals_{col}","solution_improvement_proposals",[col])
def downgrade():op.drop_table("solution_improvement_proposals");op.drop_table("presence_observations")
