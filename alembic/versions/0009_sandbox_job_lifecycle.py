"""persist sandbox job lifecycle
Revision ID: 0009_sandbox_job_lifecycle
Revises: 0008_plan_bound_projects
"""
from alembic import op
from packages.database.db import Base
from packages.database import custom_software_models
revision="0009_sandbox_job_lifecycle";down_revision="0008_plan_bound_projects";branch_labels=None;depends_on=None
def upgrade():
 bind=op.get_bind()
 for name in ("sandbox_generation_jobs","sandbox_job_events"):Base.metadata.tables[name].create(bind,checkfirst=True)
def downgrade():raise RuntimeError("Sandbox audit history downgrade is unsafe; restore a verified backup")
