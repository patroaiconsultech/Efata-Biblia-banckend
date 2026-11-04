"""Migration URL and side-effect-free schema integration regression tests."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

API_DIR = Path(__file__).resolve().parents[1]


def _alembic(args, *, environment, database_url):
    env = os.environ.copy()
    env.update({
        "BIBLE_ENVIRONMENT": environment,
        "BIBLE_AUTH_MODE": "demo",
        "PYTHONPATH": str(API_DIR / "src"),
        "DATABASE_URL": database_url,
    })
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR, env=env, capture_output=True, text=True, timeout=30,
    )


def test_migration_runs_against_explicit_database_without_premature_schema(tmp_path):
    database = tmp_path / "selected.db"
    url = f"sqlite:///{database}"
    result = _alembic(["upgrade", "head"], environment="local", database_url=url)
    assert result.returncode == 0, result.stderr[-1500:]
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "009_bible_native_auth"


def test_migration_fails_closed_in_staging_without_postgres(tmp_path):
    missing = _alembic(["current"], environment="staging", database_url="")
    assert missing.returncode != 0
    assert "BIBLE_MIGRATION_DATABASE_URL_REQUIRED" in missing.stderr

    invalid = _alembic(
        ["current"], environment="staging",
        database_url=f"sqlite:///{tmp_path / 'never_migrate.db'}",
    )
    assert invalid.returncode != 0
    assert "BIBLE_MIGRATION_POSTGRESQL_REQUIRED" in invalid.stderr
    assert not (tmp_path / "never_migrate.db").exists()
