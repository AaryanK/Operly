"""universal workflow scope and semantic event triggers

Revision ID: 0056_universal_workflow_scope_events
Revises: 0055_platform_job_idempotency_scope
"""

from alembic import op
import sqlalchemy as sa

revision = "0056_universal_workflow_scope_events"
down_revision = "0055_platform_job_idempotency_scope"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {item.get("name") for item in inspector.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("workflow_definitions"):
        columns = _columns(inspector, "workflow_definitions")
        with op.batch_alter_table("workflow_definitions") as batch:
            if "scope_kind" not in columns:
                batch.add_column(
                    sa.Column(
                        "scope_kind",
                        sa.String(20),
                        nullable=False,
                        server_default="workspace",
                    )
                )
            batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=True)
        inspector = sa.inspect(bind)
        if "ix_workflow_definitions_scope_kind" not in _indexes(inspector, "workflow_definitions"):
            op.create_index(
                "ix_workflow_definitions_scope_kind",
                "workflow_definitions",
                ["scope_kind"],
            )

    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_runs"):
        columns = _columns(inspector, "workflow_runs")
        with op.batch_alter_table("workflow_runs") as batch:
            if "scope_kind" not in columns:
                batch.add_column(
                    sa.Column(
                        "scope_kind",
                        sa.String(20),
                        nullable=False,
                        server_default="workspace",
                    )
                )
            batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=True)
        inspector = sa.inspect(bind)
        if "ix_workflow_runs_scope_kind" not in _indexes(inspector, "workflow_runs"):
            op.create_index("ix_workflow_runs_scope_kind", "workflow_runs", ["scope_kind"])

    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_trace_events"):
        columns = _columns(inspector, "workflow_trace_events")
        with op.batch_alter_table("workflow_trace_events") as batch:
            if "scope_kind" not in columns:
                batch.add_column(
                    sa.Column(
                        "scope_kind",
                        sa.String(20),
                        nullable=False,
                        server_default="workspace",
                    )
                )
            if "owner_user_id" not in columns:
                batch.add_column(
                    sa.Column(
                        "owner_user_id",
                        sa.String(36),
                        sa.ForeignKey("app_users.id", ondelete="CASCADE"),
                        nullable=True,
                    )
                )
            batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=True)
        inspector = sa.inspect(bind)
        indexes = _indexes(inspector, "workflow_trace_events")
        if "ix_workflow_trace_events_scope_kind" not in indexes:
            op.create_index(
                "ix_workflow_trace_events_scope_kind",
                "workflow_trace_events",
                ["scope_kind"],
            )
        if "ix_workflow_trace_events_owner_user_id" not in indexes:
            op.create_index(
                "ix_workflow_trace_events_owner_user_id",
                "workflow_trace_events",
                ["owner_user_id"],
            )

    inspector = sa.inspect(bind)
    if not inspector.has_table("workflow_event_triggers"):
        op.create_table(
            "workflow_event_triggers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "workflow_id",
                sa.String(36),
                sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_pattern", sa.String(160), nullable=False),
            sa.Column("condition_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "created_by_user_id",
                sa.String(36),
                sa.ForeignKey("app_users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "workflow_id",
                "event_pattern",
                name="uq_workflow_event_trigger_pattern",
            ),
        )
        op.create_index(
            "ix_workflow_event_triggers_workflow_id",
            "workflow_event_triggers",
            ["workflow_id"],
        )
        op.create_index(
            "ix_workflow_event_triggers_event_pattern",
            "workflow_event_triggers",
            ["event_pattern"],
        )
        op.create_index(
            "ix_workflow_event_triggers_enabled",
            "workflow_event_triggers",
            ["enabled"],
        )
        op.create_index(
            "ix_workflow_event_triggers_created_by_user_id",
            "workflow_event_triggers",
            ["created_by_user_id"],
        )
        op.create_index(
            "ix_workflow_event_triggers_created_at",
            "workflow_event_triggers",
            ["created_at"],
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("workflow_event_cursors"):
        op.create_table(
            "workflow_event_cursors",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("last_created_at", sa.DateTime(), nullable=True),
            sa.Column("last_event_id", sa.String(36), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_workflow_event_cursors_last_created_at",
            "workflow_event_cursors",
            ["last_created_at"],
        )
        # Begin at migration time: semantic event triggers are opt-in going forward and
        # must never retroactively replay the historical Kernel audit log.
        op.execute(
            sa.text(
                "INSERT INTO workflow_event_cursors (id, last_created_at, last_event_id, updated_at) "
                "VALUES ('kernel', CURRENT_TIMESTAMP, '', CURRENT_TIMESTAMP)"
            )
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_event_cursors"):
        op.drop_table("workflow_event_cursors")
    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_event_triggers"):
        op.drop_table("workflow_event_triggers")

    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_trace_events"):
        columns = _columns(inspector, "workflow_trace_events")
        with op.batch_alter_table("workflow_trace_events") as batch:
            batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=False)
            if "owner_user_id" in columns:
                batch.drop_column("owner_user_id")
            if "scope_kind" in columns:
                batch.drop_column("scope_kind")

    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_runs"):
        columns = _columns(inspector, "workflow_runs")
        with op.batch_alter_table("workflow_runs") as batch:
            batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=False)
            if "scope_kind" in columns:
                batch.drop_column("scope_kind")

    inspector = sa.inspect(bind)
    if inspector.has_table("workflow_definitions"):
        columns = _columns(inspector, "workflow_definitions")
        with op.batch_alter_table("workflow_definitions") as batch:
            batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=False)
            if "scope_kind" in columns:
                batch.drop_column("scope_kind")