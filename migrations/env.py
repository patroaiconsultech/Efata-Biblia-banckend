from __future__ import annotations

from logging.config import fileConfig
import os

from sqlalchemy.engine import make_url

from alembic import context
from sqlalchemy import engine_from_config, pool

from bible_platform.infrastructure.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def configure_database_url() -> None:
    """Resolve the same DATABASE_URL consumed by the Bible API.

    Never migrate the local SQLite fallback in staging/production.
    """
    environment = os.getenv("BIBLE_ENVIRONMENT", "local").strip().lower()
    url = os.getenv("DATABASE_URL", "").strip()
    if environment in {"staging", "production"} and not url:
        raise RuntimeError("BIBLE_MIGRATION_DATABASE_URL_REQUIRED")
    if not url:
        return  # Only local development may use alembic.ini's SQLite URL.
    try:
        parsed = make_url(url)
        if environment in {"staging", "production"} and parsed.get_backend_name() != "postgresql":
            raise RuntimeError("BIBLE_MIGRATION_POSTGRESQL_REQUIRED")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("BIBLE_MIGRATION_DATABASE_URL_INVALID") from exc
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    # Alembic's configparser requires percent escaping in connection URLs.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))


configure_database_url()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
