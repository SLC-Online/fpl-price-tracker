#!/bin/bash
# Double-clickable launcher for the FPL Transfer Optimizer.
# Starts the local web UI (no Tk needed) and opens it in your browser.
# You can close this Terminal window to stop the server when you're done.

cd "$(dirname "$0")" || exit 1

# Load .env (Supabase creds) if present
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

# Use the system python3 (3.9) — it has 'requests' and runs the stdlib web server.
PY=/usr/bin/python3
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
    echo "Installing 'requests' for $PY ..."
    "$PY" -m pip install --user requests
fi

clear
echo "============================================================"
echo "  FPL Transfer Optimizer"
echo "  Your browser will open at http://127.0.0.1:8765"
echo "  Leave this window open while you use it."
echo "  Close this window (or press Ctrl+C) to stop."
echo "============================================================"
echo

exec "$PY" optimizer_web.py
