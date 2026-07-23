#!/bin/bash
# One-command Cadreur setup on a Mac — fresh machine, or after copying/moving
# the project folder. Safe to re-run.
#
#   ./scripts/setup_mac.sh
#
# Why this exists: a virtualenv stores ABSOLUTE paths, so a .venv copied from
# another machine (or from another folder) fails with
#   bad interpreter: /Users/<someone>/.../.venv/bin/python3.x: no such file
# This deletes that stale .venv and rebuilds everything from scratch.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null; then
  echo "No '$PY' found. Install Python 3.11+ from https://www.python.org/downloads/"
  exit 1
fi
echo "==> Python: $("$PY" --version 2>&1)  ($(command -v "$PY"))"
if [ "$("$PY" -c 'import sys; print(sys.version_info >= (3, 11))')" != "True" ]; then
  echo "Cadreur needs Python 3.11 or newer. Install it from https://www.python.org/downloads/"
  exit 1
fi

if [ -d .venv ]; then
  echo "==> Removing the existing .venv (a copied/moved one holds dead paths)"
  rm -rf .venv
fi

echo "==> Creating .venv"
"$PY" -m venv .venv

echo "==> Installing Cadreur and its dependencies (needs internet, once)"
./.venv/bin/pip install -e '.[gui]'

if [ -f cadreur.toml ]; then
  echo "==> Keeping the existing cadreur.toml"
else
  echo "==> Creating cadreur.toml from the example"
  cp cadreur.example.toml cadreur.toml
fi

echo "==> Building Cadreur.app"
./scripts/make_app.sh

echo
echo "Done."
echo "  1. Check cadreur.toml  ->  [telemetre] url = the Pi, e.g. http://192.168.0.51"
echo "  2. Double-click Cadreur.app (keep it in this folder; drag it to the Dock)"
echo "     or run:  ./.venv/bin/python -m cadreur   then open http://127.0.0.1:8080"
