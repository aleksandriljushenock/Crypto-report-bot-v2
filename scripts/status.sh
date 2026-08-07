#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
echo "=== Docker ==="
docker compose -f docker-compose.vps.yml ps
echo
echo "=== Last 80 log lines ==="
docker compose -f docker-compose.vps.yml logs --tail=80 crypto-bot
