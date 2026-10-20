#!/bin/bash
# Consistent SQLite backup -> private S3 bucket, using the EC2 instance role (no keys).
# On EC2 it reads /etc/taskmanager.env (BACKUP_BUCKET, DB_PATH, region).
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/taskmanager.env}"
if [ -r "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi

: "${BACKUP_BUCKET:?Set BACKUP_BUCKET to the S3 bucket name (name only, no s3://)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${DB_PATH:-$HERE/tasks.db}"
PYTHON="${PYTHON:-$HERE/venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
[ -f "$DB_PATH" ] || { echo "Database not found: $DB_PATH" >&2; exit 1; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEY="backups/tasks-$STAMP.db"
TMP="$(mktemp /tmp/tasks-backup.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

# SQLite online-backup API: safe while the app is running
"$PYTHON" - "$DB_PATH" "$TMP" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
PY

aws s3 cp "$TMP" "s3://$BACKUP_BUCKET/$KEY" --sse AES256 --only-show-errors
echo "Backup uploaded: s3://$BACKUP_BUCKET/$KEY"
