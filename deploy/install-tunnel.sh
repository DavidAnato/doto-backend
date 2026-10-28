#!/bin/bash
set -euo pipefail
mkdir -p "$HOME/bin" /home/fotoce/doto-backend/logs
if [[ ! -x "$HOME/bin/cloudflared" ]]; then
  curl -fsSL -o "$HOME/bin/cloudflared" \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod 755 "$HOME/bin/cloudflared"
fi
"$HOME/bin/cloudflared" --version
sed -i 's/\r$//' /home/fotoce/doto-backend/deploy/*.sh
chmod 755 /home/fotoce/doto-backend/deploy/*.sh
chmod 600 /home/fotoce/doto-backend/deploy/cloudflare/tunnel.token
/home/fotoce/doto-backend/deploy/ensure.sh
sleep 3
ss -ltn | grep 8020 || echo "api down"
pgrep -af "cloudflared tunnel" || echo "tunnel down"
tail -n 20 /home/fotoce/doto-backend/logs/cloudflared.log || true
