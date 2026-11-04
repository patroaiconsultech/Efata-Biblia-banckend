#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m compileall -q src tests scripts
pytest -q
rm -f bible_local.db
alembic upgrade head
printf '%s\n' 'BIBLE_API_VERIFY_OK'
