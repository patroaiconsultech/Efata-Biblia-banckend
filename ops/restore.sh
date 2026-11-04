#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"
if [ "${ALLOW_DATABASE_RESTORE:-}" != "YES_I_HAVE_A_BACKUP_AND_APPROVED_RESTORE" ]; then
  echo 'Refusing restore: set ALLOW_DATABASE_RESTORE explicitly after approval.' >&2
  exit 2
fi

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DATABASE_URL" "$BACKUP_FILE"
printf '%s\n' 'RESTORE_COMPLETED_VERIFY_HEALTH_READINESS_AND_COUNTS'
