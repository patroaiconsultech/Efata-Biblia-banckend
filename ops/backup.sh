#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/bible-${STAMP}.dump"

pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" > "$OUT"
chmod 600 "$OUT"
printf 'BACKUP_CREATED=%s\n' "$OUT"
