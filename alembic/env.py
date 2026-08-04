from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from packages.database.db import Base, normalize_database_url
from packages.database.schema import import_all_models, synchronous_database_url

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

import_all_models()
target_metadata = Base.metadata


def configured_url() -> str:
    value = config.get_main_option("sqlalchemy.url") or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./operly.db")
    return synchronous_database_url(normalize_database_url(value))


def run_migrations_offline() -> None:
    context.configure(url=configured_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = configured_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.begin() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite",
                          transactional_ddl=True,
                          compare_type=True, compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
