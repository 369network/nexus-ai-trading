"""
NEXUS ALPHA — Alembic Environment Configuration
=================================================
Async-compatible Alembic env.py for SQLAlchemy 2.0 async engine.

Supports:
  - Online migrations (async, applies changes to a live DB)
  - Offline migrations (generates SQL scripts without DB connection)
  - Auto-generation of migrations from model changes

Environment variables:
  DATABASE_URL — PostgreSQL connection string
                 e.g. postgresql+asyncpg://user:pass@host:5432/nexus_alpha
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Import all models so Alembic can detect schema changes
# ---------------------------------------------------------------------------
# This import must happen before Base.metadata is accessed.
from src.db.models import Base  # noqa: F401  — registers all ORM models

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

# ---------------------------------------------------------------------------
# Set up logging from alembic.ini if it exists
# ---------------------------------------------------------------------------
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Target metadata for autogenerate support
# ---------------------------------------------------------------------------
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Database URL resolution
# ---------------------------------------------------------------------------


def get_database_url() -> str:
    """
    Resolve the database URL from:
    1. Alembic config (alembic.ini sqlalchemy.url)
    2. DATABASE_URL environment variable

    Ensures the URL uses the asyncpg driver for async operations.

    Returns:
        PostgreSQL connection URL with asyncpg driver.

    Raises:
        RuntimeError: If no database URL is configured.
    """
    url = config.get_main_option("sqlalchemy.url")

    if not url:
        url = os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "Database URL not configured. Set DATABASE_URL environment variable "
            "or configure sqlalchemy.url in alembic.ini."
        )

    # Normalise URL to use asyncpg driver
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)

    # For offline mode we need a sync URL — strip the +asyncpg for psycopg2
    return url


def get_sync_url(url: str) -> str:
    """
    Convert an asyncpg URL to a sync URL for offline SQL generation.

    Args:
        url: asyncpg connection URL.

    Returns:
        psycopg2-compatible URL for offline SQL generation.
    """
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Offline migration mode
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    In offline mode, Alembic generates SQL scripts without connecting to the
    database. Useful for reviewing migrations or running them manually.

    The generated SQL is output to stdout or the configured file.
    """
    url = get_database_url()

    context.configure(
        url=get_sync_url(url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        render_as_batch=False,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migration mode (async)
# ---------------------------------------------------------------------------


def do_run_migrations(connection: Connection) -> None:
    """
    Execute migrations using an existing connection.

    Called by the async runner after the connection is established.

    Args:
        connection: SQLAlchemy Connection to use for migrations.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        render_as_batch=False,
        # Additional options for PostgreSQL
        dialect_opts={"isolation_level": "AUTOCOMMIT"},
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Create an async engine and run migrations in 'online' mode.

    Uses NullPool to avoid connection pool issues during migrations —
    Alembic handles its own connection lifecycle.
    """
    url = get_database_url()

    configuration: dict[str, Any] = config.get_section(
        config.config_ini_section, {}
    )
    configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode (connects to the database).

    Dispatches to the async runner via asyncio.run().
    """
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
