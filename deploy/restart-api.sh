#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /home/fotoce/doto-backend/deploy/*.sh
chmod 755 /home/fotoce/doto-backend/deploy/*.sh
pkill -f "cloudflared tunnel" || true
pkill -f "gunicorn config.wsgi:application" || true
sleep 1
/home/fotoce/doto-backend/deploy/ensure.sh
sleep 2
curl -sS -m 15 http://127.0.0.1:8003/api/health/
echo
ss -ltn | grep 8003 || echo "8003 down"
