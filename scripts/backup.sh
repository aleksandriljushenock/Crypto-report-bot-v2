#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$ROOT/backups"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Local persistent state only. Secrets from .env are intentionally excluded.
tar -czf "$BACKUP_DIR/local_state_${STAMP}.tar.gz" \
  -C "$ROOT" \
  --exclude='data/*.lock' \
  data 2>/dev/null || true

# Keep 14 days of local archives.
find "$BACKUP_DIR" -type f -name 'local_state_*.tar.gz' -mtime +14 -delete

echo "Backup created: $BACKUP_DIR/local_state_${STAMP}.tar.gz"
