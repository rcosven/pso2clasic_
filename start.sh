#!/bin/sh
# Arranque Railway: siempre ejecuta el script del working dir /app.
set -e
echo "[start.sh] BOT arrancando $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exec python translate_missing.py
