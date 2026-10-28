#!/bin/bash
set -euo pipefail
cd /home/fotoce/doto-backend
ps -u fotoce -o args= | grep "gunicorn config.wsgi" | head -2
TOKEN=$(./venv/bin/python - << 'PY'
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:8003/api/auth/login/",
    data=json.dumps({"username": "medecin", "password": "mdp123"}).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=15) as res:
    print(json.loads(res.read().decode())["access"])
PY
)
echo "token_len ${#TOKEN}"
curl -sS -N -m 6 -D - -o /tmp/doto-sse-body.txt \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: text/event-stream" \
  "http://127.0.0.1:8003/api/hub/events/" || true
echo "--- body ---"
wc -c /tmp/doto-sse-body.txt
head -c 300 /tmp/doto-sse-body.txt
echo
rm -f /tmp/doto-sse-body.txt
