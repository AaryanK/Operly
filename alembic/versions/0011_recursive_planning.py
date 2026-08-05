"""persist recursive planning state
Revision ID: 0011_recursive_planning
Revises: 0010_isolated_runner_records
"""
from alembic import op
import sqlalchemy as sa

revision="0011_recursive_planning"
down_revision="0010_isolated_runner_records"
branch_labels=None
depends_on=None

def upgrade():
    existing={x["name"] for x in sa.inspect(op.get_bind()).get_columns("software_plan_versions")}
    columns=[sa.Column("requirement_ledger_json",sa.Text(),nullable=False,server_default="[]"),
        sa.Column("plan_tree_json",sa.Text(),nullable=False,server_default="[]"),
        sa.Column("validation_json",sa.Text(),nullable=False,server_default="{}"),
        sa.Column("semantic_diff_json",sa.Text(),nullable=False,server_default="{}")]
    for column in columns:
        if column.name not in existing:
            with op.batch_alter_table("software_plan_versions") as batch:batch.add_column(column)

def downgrade():
    raise RuntimeError("Recursive planning provenance downgrade is unsafe; restore a verified backup")
