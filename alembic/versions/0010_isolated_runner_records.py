"""persist isolated runner records
Revision ID: 0010_isolated_runner_records
Revises: 0009_sandbox_job_lifecycle
"""
from alembic import op
from packages.database.db import Base
from packages.database import custom_software_models
revision="0010_isolated_runner_records";down_revision="0009_sandbox_job_lifecycle";branch_labels=None;depends_on=None
TABLES=("generated_source_bundles","runner_build_records","runner_build_events","runner_artifacts","runner_previews","runner_repairs")
def upgrade():
 bind=op.get_bind()
 for name in TABLES:Base.metadata.tables[name].create(bind,checkfirst=True)
def downgrade():raise RuntimeError("Runner audit and provenance downgrade is unsafe; restore a verified backup")
