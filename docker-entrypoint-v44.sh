#!/bin/sh
set -eu
mkdir -p /app/data /app/logs
chown -R bot:bot /app/data /app/logs 2>/dev/null || true
exec gosu bot:bot /usr/bin/tini -- "$@"
