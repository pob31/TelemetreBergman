#!/usr/bin/env bash
# Pull the latest code and restart the service. Run on the Pi:
#   ~/TelemetreBergman/scripts/deploy.sh
# Or from the dev machine:
#   ssh bergman@192.168.1.36 "~/TelemetreBergman/scripts/deploy.sh"
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[deploy] git pull"
git pull --ff-only

echo "[deploy] sync deps"
.venv/bin/pip install -q -e .

echo "[deploy] restart service"
sudo systemctl restart telemetre.service
sleep 1 || true
systemctl --no-pager --lines=6 status telemetre.service || true
