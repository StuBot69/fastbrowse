#!/usr/bin/env bash
# FastBrowse setup: venv + deps + browsers. Idempotent — safe to re-run.
# Usage: ./setup.sh [--venv PATH]   (default ./.venv)
set -euo pipefail
VENV="${1:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip -q
"$VENV/bin/python" -m pip install -r requirements.txt
"$VENV/bin/python" -m playwright install chromium 2>&1 | tail -2 || true
# Camoufox fetches its own browser on first launch — nothing to preinstall.
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "wrote .env from .env.example — fill in vision endpoints, then run doctor"
fi
echo "OK: use $VENV/bin/python fb.py doctor"
