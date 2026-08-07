#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINE="17 3 * * * $ROOT/scripts/backup.sh >> $ROOT/logs/backup.log 2>&1"
( crontab -l 2>/dev/null | grep -vF "$ROOT/scripts/backup.sh" || true; echo "$LINE" ) | crontab -
echo "Daily backup cron installed: 03:17 UTC"
