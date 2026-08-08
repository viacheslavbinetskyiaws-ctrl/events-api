import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.db import Base
from app.repositories import models  # noqa: F401  (registers EventORM on Base.metadata)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Deliberately NOT derived from app.core.config.Settings.database_url: since
# Milestone 7's RLS work, the app connects as the restricted events_app role
# (subject to row-level security), while migrations need the events owner
# role (a superuser here, so DDL/GRANT/CREATE ROLE work and RLS is always
# bypassed). Defaulting to the app's own URL would try to run DDL as a role
# that can't. ALEMBIC_DATABASE_URL is the escape hatch for anything that
# needs a different target (the test suite's migration fixture, pointing
# this at events_test instead).
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get("ALEMBIC_DATABASE_URL")
    or "postgresql+asyncpg://events:events@localhost:5432/events",
)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_object(object, name, type_, reflected, compare_to):
    """Keep autogenerate from proposing DROPs for tables Alembic doesn't own.

    daily_event_counts (DBTBase, not Base) is deliberately excluded from
    target_metadata — dbt owns its DDL via `dbt run`, not Alembic. Without
    this filter, autogenerate sees any such table as an orphan ("reflected"
    from the live DB, no `compare_to` in target_metadata) and proposes
    dropping it. This applies to any future table managed outside Alembic,
    not just this one.
    """
    if type_ == "table" and reflected and compare_to is None:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
