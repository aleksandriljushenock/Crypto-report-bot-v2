#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[1/4] Pulling latest code..."
git pull --ff-only

echo "[2/4] Building Docker image..."
docker compose -f docker-compose.vps.yml build --pull

echo "[3/4] Restarting bot..."
docker compose -f docker-compose.vps.yml up -d --remove-orphans

echo "[4/4] Status..."
docker compose -f docker-compose.vps.yml ps
