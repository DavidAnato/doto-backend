#!/bin/bash
# Relance l'API et le tunnel si l'un des deux est tombé. Appelé par cron.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/logs"
if ! ss -ltn | grep -q ':8003 '; then
  nohup "$ROOT/deploy/start.sh" >> "$ROOT/logs/boot.log" 2>&1 &
fi
