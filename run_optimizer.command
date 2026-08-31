#!/bin/bash
# Double-clickable launcher for the FPL Transfer Optimizer.
# Picks a Python that has Tkinter (the GUI toolkit) so the window opens.

cd "$(dirname "$0")"

# Load .env if present (so Supabase creds are available)
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

pick_python() {
    for py in /usr/bin/python3 python3 /opt/homebrew/bin/python3; do
        if command -v "$py" >/dev/null 2>&1; then
            if "$py" -c "import tkinter" >/dev/null 2>&1; then
                echo "$py"
                return 0
            fi
        fi
    done
    # No Tk anywhere — return plain python3 for the CLI fallback
    echo "python3"
}

PY=$(pick_python)
echo "Using: $PY"

# Ensure requests is installed for that interpreter
if ! "$PY" -c "import requests" >/dev/null 2>&1; then
    echo "Installing 'requests'…"
    "$PY" -m pip install --user requests
fi

"$PY" optimizer_app.py "$@"
