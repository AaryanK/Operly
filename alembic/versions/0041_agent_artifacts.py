"""add durable artifacts and shared agent run checkpoints

Revision ID: 0041_agent_artifacts
Revises: 0040_business_event_scope
"""

from alembic import op
import sqlalchemy as sa

revision = "0041_agent_artifacts"
down_revision = "0040_business_event_scope"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    # The historical 0001 baseline dynamically creates every currently registered
    # Base model when bootstrapping a brand-new database. Existing production
    # databases reaching this migration do not have these tables. Therefore this
    # migration must create the tables when absent and safely accept them when a
    # fresh baseline already did.
    if not _has_table("operly_artifacts"):
        op.create_table(
            "operly_artifacts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("scope_kind", sa.String(length=20), nullable=False),
            sa.Column("scope_id", sa.String(length=220), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("owner_user_id", sa.String(length=120), nullable=True),
            sa.Column("run_id", sa.String(length=120), nullable=True),
            sa.Column("parent_artifact_id", sa.String(length=36), nullable=True),
            sa.Column("created_by", sa.String(length=120), nullable=True),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=200), nullable=False, server_default="application/octet-stream"),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False, server_default="agent"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("storage_kind", sa.String(length=32), nullable=False, server_default="database"),
            sa.Column("storage_key", sa.String(length=500), nullable=True),
            sa.Column("content_bytes", sa.LargeBinary(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) OR "
                "(scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
                name="ck_operly_artifact_scope_owner",
            ),
        )
        for name, columns in (
            ("ix_operly_artifacts_scope_kind", ["scope_kind"]),
            ("ix_operly_artifacts_scope_id", ["scope_id"]),
            ("ix_operly_artifacts_tenant_id", ["tenant_id"]),
            ("ix_operly_artifacts_owner_user_id", ["owner_user_id"]),
            ("ix_operly_artifacts_run_id", ["run_id"]),
            ("ix_operly_artifacts_sha256", ["sha256"]),
            ("ix_operly_artifacts_expires_at", ["expires_at"]),
            ("ix_operly_artifact_scope_created", ["scope_kind", "scope_id", "created_at"]),
            ("ix_operly_artifact_scope_sha", ["scope_kind", "scope_id", "sha256"]),
        ):
            op.create_index(name, "operly_artifacts", columns)

    if not _has_table("agent_runs"):
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.String(length=120), primary_key=True),
            sa.Column("scope_kind", sa.String(length=20), nullable=False),
            sa.Column("scope_id", sa.String(length=220), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=True),
            sa.Column("owner_user_id", sa.String(length=120), nullable=True),
            sa.Column("actor_id", sa.String(length=120), nullable=True),
            sa.Column("surface", sa.String(length=60), nullable=False, server_default="unknown"),
            sa.Column("channel", sa.String(length=60), nullable=False, server_default="operly"),
            sa.Column("conversation_id", sa.String(length=255), nullable=True),
            sa.Column("workflow_job_id", sa.String(length=120), nullable=True),
            sa.Column("objective", sa.Text(), nullable=False, server_default=""),
            sa.Column("state", sa.String(length=32), nullable=False, server_default="running"),
            sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("checkpoint_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("artifact_refs_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("pending_approval_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) OR "
                "(scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
                name="ck_agent_runs_scope_owner",
            ),
        )
        for name, columns in (
            ("ix_agent_runs_scope_kind", ["scope_kind"]),
            ("ix_agent_runs_scope_id", ["scope_id"]),
            ("ix_agent_runs_tenant_id", ["tenant_id"]),
            ("ix_agent_runs_owner_user_id", ["owner_user_id"]),
            ("ix_agent_runs_conversation_id", ["conversation_id"]),
            ("ix_agent_runs_workflow_job_id", ["workflow_job_id"]),
            ("ix_agent_runs_state", ["state"]),
            ("ix_agent_run_scope_updated", ["scope_kind", "scope_id", "updated_at"]),
        ):
            op.create_index(name, "agent_runs", columns)

    if not _has_table("agent_run_events"):
        op.create_table(
            "agent_run_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("run_id", sa.String(length=120), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        )
        op.create_index("ix_agent_run_events_run_id", "agent_run_events", ["run_id"])
        op.create_index("ix_agent_run_event_run_created", "agent_run_events", ["run_id", "created_at"])


def downgrade():
    for table in ("agent_run_events", "agent_runs", "operly_artifacts"):
        if _has_table(table):
            op.drop_table(table)
