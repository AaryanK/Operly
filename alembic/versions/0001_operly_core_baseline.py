"""Operly core schema baseline.

Revision ID: 0001_operly_core
Revises: none
"""
from alembic import op

revision = "0001_operly_core"
down_revision = None
branch_labels = None
depends_on = None

DASHBOARD_TABLES = {
    "dashboard_customizations", "dashboard_change_sets",
    "dashboard_change_operations", "app_configuration_versions",
    "dashboard_studio_audits",
}
APPLICATION_BUILDER_TABLES = {
    "managed_applications", "application_versions", "application_change_sets",
    "managed_records", "application_audit_events", "application_preview_sessions",
}
CUSTOM_SOFTWARE_TABLES = {
    "generated_projects", "generated_project_change_sets", "service_customers", "service_requests", "service_status_events",
}


def upgrade() -> None:
    # This initial baseline captures every pre-Dashboard-Studio model registered at
    # release time. Later model changes must always receive their own revision.
    from packages.database.db import Base
    from packages.database.schema import import_all_models
    import_all_models()
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name not in DASHBOARD_TABLES | APPLICATION_BUILDER_TABLES | CUSTOM_SOFTWARE_TABLES and table.name != "alembic_version":
            table.create(bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Core baseline downgrade is intentionally unsupported; restore a verified backup")
