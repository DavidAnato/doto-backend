#!/bin/bash
# Pré-préprod DOTO+ : un process, SQLite, port 8020 (origine du Worker).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
if ss -ltn | grep -q ':8003 '; then
  echo "doto-api already listening on 8003"
  exit 0
fi
# Un seul process : le bus SSE est en mémoire.
# gthread : chaque flux SSE occupe un thread pendant toute sa durée.
# 4 threads suffisaient à bloquer /api/health/ dès qu'un hub et une app
# patient restaient connectés. 24 laisse de la place aux uploads et à la carte.
exec "$ROOT/venv/bin/gunicorn" config.wsgi:application \
  --bind 0.0.0.0:8003 \
  --workers 1 \
  --worker-class gthread \
  --threads 24 \
  --timeout 120 \
  --graceful-timeout 20 \
  --keep-alive 65 \
  --access-logfile "$ROOT/logs/access.log" \
  --error-logfile "$ROOT/logs/error.log"
