"""stable solution registry
Revision ID: 0018_solution_registry
Revises: 0017_company_intelligence
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime
from uuid import uuid4
revision="0018_solution_registry";down_revision="0017_company_intelligence";branch_labels=None;depends_on=None
def upgrade():
 tables=set(sa.inspect(op.get_bind()).get_table_names())
 if "solutions" not in tables:
  op.create_table("solutions",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("name",sa.String(200),nullable=False),sa.Column("description",sa.Text(),nullable=False),sa.Column("solution_type",sa.String(60),nullable=False),sa.Column("lifecycle_status",sa.String(30),nullable=False),sa.Column("runtime_type",sa.String(40),nullable=False),sa.Column("runtime_reference",sa.String(120),nullable=False),sa.Column("current_version_reference",sa.String(120)),sa.Column("preview_state",sa.String(30),nullable=False),sa.Column("preview_url",sa.Text()),sa.Column("production_state",sa.String(30),nullable=False),sa.Column("production_url",sa.Text()),sa.Column("visibility",sa.String(30),nullable=False),sa.Column("context_json",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),sa.Column("updated_at",sa.DateTime(),nullable=False),sa.UniqueConstraint("tenant_id","runtime_type","runtime_reference",name="uq_solution_runtime_identity"));op.create_index("ix_solutions_tenant_id","solutions",["tenant_id"]);op.create_index("ix_solutions_solution_type","solutions",["solution_type"]);op.create_index("ix_solutions_lifecycle_status","solutions",["lifecycle_status"])
 _backfill(op.get_bind())
def _backfill(bind):
 metadata=sa.MetaData();solutions=sa.Table("solutions",metadata,autoload_with=bind);now=datetime.utcnow()
 mappings=(("studio_projects","studio","digital_presence","active_draft_version_id","description"),("managed_applications","managed_app","business_app","active_version_id","description"),("generated_projects","generated_project","custom_solution","version","prompt"))
 available=set(sa.inspect(bind).get_table_names())
 for table_name,runtime_type,solution_type,version_column,description_column in mappings:
  if table_name not in available:continue
  source=sa.Table(table_name,metadata,autoload_with=bind)
  for item in bind.execute(sa.select(source)).mappings():
   exists=bind.scalar(sa.select(sa.func.count()).select_from(solutions).where(solutions.c.tenant_id==item["tenant_id"],solutions.c.runtime_type==runtime_type,solutions.c.runtime_reference==item["id"]))
   if exists:continue
   version=item.get(version_column);ready=bool(version)
   bind.execute(solutions.insert().values(id=str(uuid4()),tenant_id=item["tenant_id"],name=item.get("name") or "Untitled",description=(item.get(description_column) or "")[:4000],solution_type=solution_type,lifecycle_status="preview_ready" if ready else "draft",runtime_type=runtime_type,runtime_reference=item["id"],current_version_reference=str(version) if version is not None else None,preview_state="ready" if ready or runtime_type=="generated_project" else "unavailable",preview_url="/api/solutions/{solution_id}/preview" if ready or runtime_type=="generated_project" else None,production_state="offline",production_url=None,visibility="private",context_json="{}",created_at=item.get("created_at") or now,updated_at=item.get("updated_at") or item.get("created_at") or now))
def downgrade():op.drop_table("solutions")
