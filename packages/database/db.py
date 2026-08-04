import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
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


async def init_db():
    from packages.database import models  # noqa: F401
    from packages.database import operations_models  # noqa: F401
    from packages.database import agent_models  # noqa: F401
    from packages.database import business_models  # noqa: F401
    from packages.database import studio_models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
