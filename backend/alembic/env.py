"""Alembic environment.

Pulls the database URL from application settings rather than alembic.ini, and
imports `app.models` so autogenerate sees every mapped table.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.core.config import settings
from app.core.database import Base, build_engine

# Registers all mapped classes on Base.metadata. Required for autogenerate.
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve the target database URL.

    Application settings are the default so that the app, the tests, and
    migrations can never disagree. An explicit `sqlalchemy.url` (set on the
    Config object programmatically, or with `-x`/`--url` style overrides) wins,
    which is how the migration-drift test points Alembic at a scratch database.
    """
    override = config.get_main_option("sqlalchemy.url", None)
    return override or settings.DATABASE_URL


DATABASE_URL = get_database_url()

#: SQLite cannot ALTER most things in place; batch mode rewrites the table
#: instead. Harmless on PostgreSQL, essential for local development.
RENDER_AS_BATCH = DATABASE_URL.startswith("sqlite")


def render_item(type_, obj, autogen_context):
    """Keep generated migrations dependent on `sa` alone.

    Autogenerate renders a custom `TypeDecorator` with its full dotted path
    (`app.core.database.UTCDateTime(...)`) but does not emit the corresponding
    import, so the migration raises `NameError` the first time it runs. It also
    couples the migration to application code, which is wrong on principle: a
    migration must keep working even if the model layer is later refactored or
    the class is renamed.

    Rendering the decorator as its underlying `impl` fixes both. The DB-level
    type is identical, so this does not change the emitted DDL and does not show
    up as schema drift.
    """
    from app.core.database import UTCDateTime

    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.DateTime(timezone=True)"

    # False means "fall back to the default rendering".
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=RENDER_AS_BATCH,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = build_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=RENDER_AS_BATCH,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
