from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from shared.config import load_config
from shared.models import Base

target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve the migration database URL from layered settings."""
    return load_config().env.database_url


def run_migrations_offline() -> None:
    """Run migrations without creating a database engine."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a database connection."""
    configuration = context.config.get_section(context.config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


def _is_alembic_environment() -> bool:
    try:
        return context.config is not None
    except (AttributeError, NameError, RuntimeError):
        return False


if _is_alembic_environment():
    if context.config.config_file_name is not None:
        fileConfig(context.config.config_file_name)

    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
