import asyncio
import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase


def normalize_database_url(url: str) -> str:
    """Convert Railway-style PostgreSQL URLs to SQLAlchemy's asyncpg driver."""
    value = url.strip()
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


DATABASE_URL = normalize_database_url(
    os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./operly.db",
    )
)

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


@asynccontextmanager
async def session_scope():
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def assert_schema_current(connection) -> None:
    from packages.database.schema import ALEMBIC_HEAD

    try:
        current = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception as error:
        raise RuntimeError(
            "Production database is unversioned; run the documented Alembic upgrade before startup"
        ) from error
    if current != ALEMBIC_HEAD:
        raise RuntimeError(
            f"Production database revision is incompatible; expected {ALEMBIC_HEAD}"
        )


def _auto_migrate_enabled() -> bool:
    configured = os.getenv("OPERLY_AUTO_MIGRATE")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    # Railway services commonly run a single application instance during this
    # migration phase. Defaulting on there makes copied/fresh deployments boot
    # even when the platform overrides the Dockerfile start command.
    return bool(os.getenv("RAILWAY_PUBLIC_DOMAIN"))


def _upgrade_to_head() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


async def init_db():
    from packages.database.schema import import_all_models

    import_all_models()

    environment = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower()
    if environment in {"production", "prod"}:
        if _auto_migrate_enabled():
            await asyncio.to_thread(_upgrade_to_head)
        async with engine.connect() as connection:
            await assert_schema_current(connection)
        return

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
