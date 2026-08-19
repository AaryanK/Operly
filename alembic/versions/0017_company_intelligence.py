"""durable company intelligence
Revision ID: 0017_company_intelligence
Revises: 0016_operating_product
"""
from alembic import op
import sqlalchemy as sa
revision="0017_company_intelligence";down_revision="0016_operating_product";branch_labels=None;depends_on=None
def upgrade():
 inspect=sa.inspect(op.get_bind());tables=set(inspect.get_table_names());columns={x["name"] for x in inspect.get_columns("company_profiles")}
 with op.batch_alter_table("company_profiles") as b:
  if "profile_json" not in columns:b.add_column(sa.Column("profile_json",sa.Text(),nullable=False,server_default="{}"))
  if "field_meta_json" not in columns:b.add_column(sa.Column("field_meta_json",sa.Text(),nullable=False,server_default="{}"))
 if "company_evidence" not in tables:
  op.create_table("company_evidence",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("field_key",sa.String(120),nullable=False),sa.Column("value_json",sa.Text(),nullable=False),sa.Column("source_type",sa.String(40),nullable=False),sa.Column("source_url",sa.Text()),sa.Column("source_reference",sa.String(255)),sa.Column("confidence",sa.Float(),nullable=False),sa.Column("observed_at",sa.DateTime(),nullable=False),sa.Column("owner_confirmed",sa.Boolean(),nullable=False),sa.Column("superseded",sa.Boolean(),nullable=False),sa.Column("stale",sa.Boolean(),nullable=False),sa.Column("content_hash",sa.String(64),nullable=False),sa.UniqueConstraint("tenant_id","field_key","content_hash","source_type",name="uq_company_evidence_fact"));op.create_index("ix_company_evidence_tenant_id","company_evidence",["tenant_id"]);op.create_index("ix_company_evidence_field_key","company_evidence",["field_key"]);op.create_index("ix_company_evidence_observed_at","company_evidence",["observed_at"])
 if "company_questions" not in tables:
  op.create_table("company_questions",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("question",sa.Text(),nullable=False),sa.Column("why_it_matters",sa.Text(),nullable=False),sa.Column("target_fields_json",sa.Text(),nullable=False),sa.Column("business_type",sa.String(80)),sa.Column("answered",sa.Boolean(),nullable=False),sa.Column("answer_json",sa.Text()),sa.Column("owner_confirmed",sa.Boolean(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("answered_at",sa.DateTime()));op.create_index("ix_company_questions_tenant_id","company_questions",["tenant_id"]);op.create_index("ix_company_questions_answered","company_questions",["answered"])
 if "company_research_runs" not in tables:
  op.create_table("company_research_runs",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("seed",sa.Text(),nullable=False),sa.Column("status",sa.String(30),nullable=False),sa.Column("searches_used",sa.Float(),nullable=False),sa.Column("pages_used",sa.Float(),nullable=False),sa.Column("completion_reason",sa.String(120)),sa.Column("summary_json",sa.Text(),nullable=False),sa.Column("started_at",sa.DateTime(),nullable=False),sa.Column("completed_at",sa.DateTime()));op.create_index("ix_company_research_runs_tenant_id","company_research_runs",["tenant_id"]);op.create_index("ix_company_research_runs_status","company_research_runs",["status"])
def downgrade():
 op.drop_table("company_research_runs");op.drop_table("company_questions");op.drop_table("company_evidence")
 with op.batch_alter_table("company_profiles") as b:b.drop_column("field_meta_json");b.drop_column("profile_json")
