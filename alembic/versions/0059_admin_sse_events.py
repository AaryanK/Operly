"""Publish committed admin data changes across API and worker processes.

Revision ID: 0059_admin_sse_events
Revises: 0058_agent_chat_history

PostgreSQL NOTIFY publishes *only invalidation topics*, never account or usage data.
Notifications become visible after the modifying transaction commits.
"""

from alembic import op

revision = "0059_admin_sse_events"
down_revision = "0058_agent_chat_history"
branch_labels = None
depends_on = None

CHANNEL = "operly_admin_invalidation"


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE FUNCTION operly_admin_emit_invalidation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE topic text;
        BEGIN
            FOREACH topic IN ARRAY TG_ARGV LOOP
                PERFORM pg_notify('operly_admin_invalidation', topic);
            END LOOP;
            RETURN NULL;
        END;
        $$
        """
    )
    # Statement triggers avoid one notification per row for bulk changes.
    for table, topics in (
        ("app_users", ("overview", "users")),
        ("tenants", ("overview", "workspaces", "users")),
        ("tenant_members", ("overview", "workspaces", "users")),
        ("auth_sessions", ("users",)),
    ):
        arguments = ", ".join(f"'{topic}'" for topic in topics)
        op.execute(
            f"CREATE TRIGGER operly_admin_{table}_changed "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION operly_admin_emit_invalidation({arguments})"
        )

    # Successful product activity drives DAU/WAU/MAU, geographic activity,
    # and last-seen country. Failed authentication events are not dashboard data.
    op.execute(
        """
        CREATE TRIGGER operly_admin_security_events_changed
        AFTER INSERT ON security_events
        FOR EACH ROW
        WHEN (NEW.outcome = 'succeeded' AND NEW.event_type IN (
            'product_page_view', 'product_heartbeat',
            'login_success', 'google_authentication_success'
        ))
        EXECUTE FUNCTION operly_admin_emit_invalidation('overview', 'users')
        """
    )
    op.execute(
        """
        CREATE TRIGGER operly_admin_model_runtime_traces_changed
        AFTER INSERT ON model_runtime_traces
        FOR EACH ROW
        WHEN (NEW.phase = 'success' AND COALESCE(NEW.surface, '') <> 'studio')
        EXECUTE FUNCTION operly_admin_emit_invalidation('ai-usage')
        """
    )
    op.execute(
        """
        CREATE TRIGGER operly_admin_studio_model_traces_changed
        AFTER INSERT ON studio_model_traces
        FOR EACH ROW
        WHEN (NEW.phase = 'response')
        EXECUTE FUNCTION operly_admin_emit_invalidation('ai-usage')
        """
    )


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in (
        "app_users", "tenants", "tenant_members", "auth_sessions",
        "security_events", "model_runtime_traces", "studio_model_traces",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS operly_admin_{table}_changed ON {table}")
    op.execute("DROP FUNCTION IF EXISTS operly_admin_emit_invalidation()")
