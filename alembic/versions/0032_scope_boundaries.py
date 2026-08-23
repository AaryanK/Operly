"""add explicit account, solution, intelligence and provenance scope boundaries

Revision ID: 0032_scope_boundaries
Revises: 0031_studio_model_trace
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0032_scope_boundaries"
down_revision = "0031_studio_model_trace"
branch_labels = None
depends_on = None


def _id() -> str:
    return str(uuid4())


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Accounts may exist without selecting or belonging to a workspace. Existing
    # rows remain unchanged; this only removes the schema-level requirement.
    auth_columns = {item["name"]: item for item in inspector.get_columns("auth_sessions")}
    if auth_columns.get("tenant_id", {}).get("nullable") is False:
        with op.batch_alter_table("auth_sessions") as batch:
            batch.alter_column("tenant_id", existing_type=sa.String(length=36), nullable=True)

    if not inspector.has_table("profile_subjects"):
        op.create_table(
            "profile_subjects",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("reference_id", sa.String(length=120), nullable=True),
            sa.Column("display_name", sa.String(length=200), nullable=False),
            sa.Column("inherits_workspace", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "kind", "reference_id", name="uq_profile_subject_identity"),
        )
        op.create_index("ix_profile_subjects_tenant_id", "profile_subjects", ["tenant_id"])
        op.create_index("ix_profile_subjects_kind", "profile_subjects", ["kind"])
        op.create_index("ix_profile_subjects_reference_id", "profile_subjects", ["reference_id"])
        op.create_index("ix_profile_subject_tenant_kind", "profile_subjects", ["tenant_id", "kind"])

    if not inspector.has_table("scoped_company_profiles"):
        op.create_table(
            "scoped_company_profiles",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("subject_id", sa.String(length=36), nullable=False, unique=True),
            sa.Column("profile_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("field_meta_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("unresolved_conflicts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["subject_id"], ["profile_subjects.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_scoped_company_profiles_tenant_id", "scoped_company_profiles", ["tenant_id"])
        op.create_index("ix_scoped_company_profiles_subject_id", "scoped_company_profiles", ["subject_id"], unique=True)

    if not inspector.has_table("scoped_company_evidence"):
        op.create_table(
            "scoped_company_evidence",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("subject_id", sa.String(length=36), nullable=False),
            sa.Column("field_key", sa.String(length=120), nullable=False),
            sa.Column("value_json", sa.Text(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_reference", sa.String(length=255), nullable=True),
            sa.Column("actor_user_id", sa.String(length=36), nullable=True),
            sa.Column("conversation_id", sa.String(length=120), nullable=True),
            sa.Column("action_id", sa.String(length=120), nullable=True),
            sa.Column("research_run_id", sa.String(length=36), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("owner_initiated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("owner_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("observed_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["subject_id"], ["profile_subjects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_user_id"], ["app_users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("subject_id", "field_key", "content_hash", "source_type", name="uq_scoped_company_evidence_fact"),
        )
        for column in ("tenant_id", "subject_id", "field_key", "actor_user_id", "conversation_id", "action_id", "research_run_id", "observed_at"):
            op.create_index(f"ix_scoped_company_evidence_{column}", "scoped_company_evidence", [column])
        op.create_index("ix_scoped_evidence_subject_field", "scoped_company_evidence", ["subject_id", "field_key", "stale", "superseded"])

    if not inspector.has_table("solution_context_snapshots"):
        op.create_table(
            "solution_context_snapshots",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("solution_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("run_id", sa.String(length=36), nullable=True),
            sa.Column("owner_objective", sa.Text(), nullable=False, server_default=""),
            sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("context_digest", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["app_users.id"], ondelete="SET NULL"),
        )
        for column in ("tenant_id", "solution_id", "project_id", "run_id", "context_digest"):
            op.create_index(f"ix_solution_context_snapshots_{column}", "solution_context_snapshots", [column])

    if not inspector.has_table("studio_model_attempts"):
        op.create_table(
            "studio_model_attempts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("model_turn_index", sa.Integer(), nullable=False),
            sa.Column("provider_attempt_index", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=80), nullable=False),
            sa.Column("model_resource_id", sa.String(length=200), nullable=True),
            sa.Column("provider_model_id", sa.String(length=200), nullable=True),
            sa.Column("outcome", sa.String(length=40), nullable=False),
            sa.Column("error_classification", sa.String(length=80), nullable=True),
            sa.Column("failover_reason", sa.String(length=300), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("usage_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_id"], ["studio_agent_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("run_id", "model_turn_index", "provider_attempt_index", name="uq_studio_model_attempt_order"),
        )
        op.create_index("ix_studio_model_attempts_tenant_id", "studio_model_attempts", ["tenant_id"])
        op.create_index("ix_studio_model_attempts_run_id", "studio_model_attempts", ["run_id"])

    if not inspector.has_table("conversation_artifacts"):
        op.create_table(
            "conversation_artifacts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("principal_id", sa.String(length=36), nullable=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=True),
            sa.Column("channel", sa.String(length=40), nullable=False),
            sa.Column("conversation_id", sa.String(length=255), nullable=False),
            sa.Column("external_message_id", sa.String(length=255), nullable=True),
            sa.Column("artifact_kind", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("mime_type", sa.String(length=120), nullable=True),
            sa.Column("source_reference", sa.Text(), nullable=True),
            sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("content_digest", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        )
        for column in ("principal_id", "tenant_id", "channel", "conversation_id", "external_message_id", "artifact_kind", "created_at", "expires_at"):
            op.create_index(f"ix_conversation_artifacts_{column}", "conversation_artifacts", [column])
        op.create_index("ix_conversation_artifact_scope", "conversation_artifacts", ["channel", "conversation_id", "created_at"])

    if not inspector.has_table("personal_workspace_delegations"):
        op.create_table(
            "personal_workspace_delegations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("principal_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("capability_id", sa.String(length=160), nullable=False),
            sa.Column("connector_reference", sa.String(length=120), nullable=True),
            sa.Column("scope_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("grant_type", sa.String(length=30), nullable=False, server_default="persistent"),
            sa.Column("action_id", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        )
        for column in ("principal_id", "user_id", "tenant_id", "capability_id", "action_id", "status", "expires_at", "revoked_at"):
            op.create_index(f"ix_personal_workspace_delegations_{column}", "personal_workspace_delegations", [column])
        op.create_index("ix_personal_delegation_lookup", "personal_workspace_delegations", ["tenant_id", "user_id", "capability_id", "status"])

    if not inspector.has_table("delegated_capability_audit"):
        op.create_table(
            "delegated_capability_audit",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("delegation_id", sa.String(length=36), nullable=False),
            sa.Column("principal_id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("capability_id", sa.String(length=160), nullable=False),
            sa.Column("action_id", sa.String(length=120), nullable=True),
            sa.Column("outcome", sa.String(length=40), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("used_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["delegation_id"], ["personal_workspace_delegations.id"], ondelete="CASCADE"),
        )
        for column in ("delegation_id", "principal_id", "tenant_id", "action_id", "used_at"):
            op.create_index(f"ix_delegated_capability_audit_{column}", "delegated_capability_audit", [column])

    # Preserve legacy tenant-wide facts as explicitly workspace-scoped facts. We do
    # not guess which existing facts belong to a Solution; that would silently
    # broaden/reassign semantics during migration.
    now = sa.func.now()
    tenants = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants"))]
    for tenant_id in tenants:
        subject_id = _id()
        bind.execute(
            sa.text(
                "INSERT INTO profile_subjects (id, tenant_id, kind, reference_id, display_name, inherits_workspace, created_at, updated_at) "
                "VALUES (:id, :tenant, 'workspace', NULL, 'Workspace company', :inherits, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": subject_id, "tenant": tenant_id, "inherits": True},
        )
        profile = bind.execute(
            sa.text("SELECT profile_json, field_meta_json FROM company_profiles WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        ).first()
        if profile:
            bind.execute(
                sa.text(
                    "INSERT INTO scoped_company_profiles (id, tenant_id, subject_id, profile_json, field_meta_json, unresolved_conflicts_json, updated_at) "
                    "VALUES (:id, :tenant, :subject, :profile, :meta, '[]', CURRENT_TIMESTAMP)"
                ),
                {"id": _id(), "tenant": tenant_id, "subject": subject_id, "profile": profile[0] or "{}", "meta": profile[1] or "{}"},
            )
        evidence_rows = bind.execute(
            sa.text(
                "SELECT field_key, value_json, source_type, source_url, source_reference, confidence, observed_at, owner_confirmed, superseded, stale, content_hash "
                "FROM company_evidence WHERE tenant_id=:tenant"
            ),
            {"tenant": tenant_id},
        ).all()
        for item in evidence_rows:
            bind.execute(
                sa.text(
                    "INSERT INTO scoped_company_evidence "
                    "(id, tenant_id, subject_id, field_key, value_json, source_type, source_url, source_reference, actor_user_id, conversation_id, action_id, research_run_id, confidence, owner_initiated, owner_confirmed, superseded, stale, content_hash, observed_at) "
                    "VALUES (:id,:tenant,:subject,:field,:value,:stype,:url,:ref,NULL,NULL,NULL,NULL,:confidence,:initiated,:confirmed,:superseded,:stale,:hash,:observed)"
                ),
                {
                    "id": _id(), "tenant": tenant_id, "subject": subject_id, "field": item[0], "value": item[1],
                    "stype": item[2], "url": item[3], "ref": item[4], "confidence": item[5], "observed": item[6],
                    "initiated": bool(item[7]), "confirmed": bool(item[7]), "superseded": bool(item[8]), "stale": bool(item[9]), "hash": item[10],
                },
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in (
        "delegated_capability_audit",
        "personal_workspace_delegations",
        "conversation_artifacts",
        "studio_model_attempts",
        "solution_context_snapshots",
        "scoped_company_evidence",
        "scoped_company_profiles",
        "profile_subjects",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
    with op.batch_alter_table("auth_sessions") as batch:
        batch.alter_column("tenant_id", existing_type=sa.String(length=36), nullable=False)
