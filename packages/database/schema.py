"""Shared schema registration and URL helpers for runtime and Alembic."""

ALEMBIC_HEAD = "0040_platform_analytics"


def import_all_models() -> None:
    from packages.database import models  # noqa: F401
    from packages.database import operations_models  # noqa: F401
    from packages.database import agent_models  # noqa: F401
    from packages.database import business_models  # noqa: F401
    from packages.database import studio_models  # noqa: F401
    from packages.database import studio_source_models  # noqa: F401
    from packages.database import dashboard_studio_models  # noqa: F401
    from packages.database import application_builder_models  # noqa: F401
    from packages.database import custom_software_models  # noqa: F401
    from packages.database import architecture_pack_models  # noqa: F401
    from packages.database import company_models  # noqa: F401
    from packages.database import connector_models  # noqa: F401
    from packages.database import account_connector_models  # noqa: F401
    from packages.database import channel_models  # noqa: F401
    from packages.database import product_models  # noqa: F401
    from packages.database import workspace_security_models  # noqa: F401
    from packages.database import principal_models  # noqa: F401
    from packages.database import software_project_models  # noqa: F401
    from packages.database import scope_models  # noqa: F401
    from packages.database import model_trace_models  # noqa: F401
    from packages.database import analytics_models  # noqa: F401


def synchronous_database_url(url: str) -> str:
    if url.startswith("sqlite+aiosqlite:"):
        return url.replace("sqlite+aiosqlite:", "sqlite:", 1)
    if url.startswith("postgresql+asyncpg:"):
        return url.replace("postgresql+asyncpg:", "postgresql+psycopg:", 1)
    if url.startswith("postgresql:"):
        return url.replace("postgresql:", "postgresql+psycopg:", 1)
    return url