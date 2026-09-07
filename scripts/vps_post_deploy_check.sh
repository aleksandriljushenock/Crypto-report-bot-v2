#!/usr/bin/env bash
set -euo pipefail
cd "${APP_DIR:-/opt/crypto-report-bot}"
COMPOSE="docker compose -f docker-compose.vps.yml"
$COMPOSE ps --status running crypto-bot | grep -q crypto-report-bot
sleep "${HEALTH_WAIT_SECONDS:-20}"
LOGS="$($COMPOSE logs --since=3m crypto-bot 2>&1 || true)"
if printf '%s' "$LOGS" | grep -Eqi 'Traceback \(most recent call last\)|ModuleNotFoundError|ImportError|SyntaxError|Killed|OutOfMemory|OOMKilled'; then
  printf '%s\n' "$LOGS" | tail -200
  exit 2
fi
$COMPOSE exec -T crypto-bot python3 -m compileall -q /app
printf 'post-deploy-check: PASS\n'
