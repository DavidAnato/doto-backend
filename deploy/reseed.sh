#!/bin/bash
set -euo pipefail
cd /home/fotoce/doto-backend
pkill -f "gunicorn config.wsgi:application" || true
sleep 1
rm -f db.sqlite3 db.sqlite3-wal db.sqlite3-shm
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py seeddata
./deploy/ensure.sh
sleep 2
curl -sS -m 15 http://127.0.0.1:8003/api/health/
echo
