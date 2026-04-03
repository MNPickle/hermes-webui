#!/bin/bash
# ── Hermes Web UI Launcher ──────────────────────────────
# Usage: ./start.sh          (starts on port 5000)
#        ./start.sh 8080     (starts on custom port)
#        DEV=1 ./start.sh    (use Flask dev server)

PORT="${1:-${PORT:-5000}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"
WEBUI_VENV="${WEBUI_VENV:-$HOME/.hermes/.venv}"
PYTHON_BIN="$WEBUI_VENV/bin/python"
FLASK_BIN="$WEBUI_VENV/bin/flask"
GUNICORN_BIN="$WEBUI_VENV/bin/gunicorn"

echo "=========================================="
echo "  Hermes Agent Web UI"
echo "  http://127.0.0.1:$PORT"
echo "=========================================="
echo ""

cd "$APP_DIR" || exit 1

if [ ! -x "$PYTHON_BIN" ]; then
    echo "  Python runtime not found at $PYTHON_BIN"
    echo "  Set WEBUI_VENV to the Hermes/web UI virtualenv and try again."
    echo "=========================================="
    exit 1
fi

# Check if already running on this port
if curl -s "http://127.0.0.1:$PORT/" > /dev/null 2>&1; then
    echo "  Web UI is already running on port $PORT"
    echo "  Open http://127.0.0.1:$PORT in your browser"
    echo "=========================================="
    exit 0
fi

# Export repo .env for child processes launched by this script.
# app.py also loads the same file on import, so direct gunicorn stays consistent.
if [ -f "$APP_DIR/.env" ]; then
    set -a; . "$APP_DIR/.env"; set +a
fi

# Use gunicorn for production, Flask dev server only if DEV=1
if [ "${DEV}" = "1" ]; then
    echo "  [DEV MODE] Using Flask development server"
    if [ -x "$FLASK_BIN" ]; then
        FLASK_APP="$APP_DIR/app.py" "$FLASK_BIN" run --host 127.0.0.1 --port "$PORT" &
    else
        FLASK_APP="$APP_DIR/app.py" "$PYTHON_BIN" -m flask run --host 127.0.0.1 --port "$PORT" &
    fi
    SERVER_PID=$!
else
    echo "  [PRODUCTION] Using gunicorn"
    CHAT_TIMEOUT="${HERMES_CHAT_TIMEOUT:-300}"
    GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-$((CHAT_TIMEOUT + 30))}"
    if [ -d /dev/shm ]; then
        GUNICORN_WORKER_TMP="${GUNICORN_WORKER_TMP:-/dev/shm}"
    else
        GUNICORN_WORKER_TMP="${GUNICORN_WORKER_TMP:-/tmp}"
    fi
    if [ -x "$GUNICORN_BIN" ]; then
        GUNICORN_CMD=("$GUNICORN_BIN")
    else
        GUNICORN_CMD=("$PYTHON_BIN" -m gunicorn)
    fi
    "${GUNICORN_CMD[@]}" \
        --bind "127.0.0.1:$PORT" \
        --workers 2 \
        --chdir "$APP_DIR" \
        --worker-tmp-dir "$GUNICORN_WORKER_TMP" \
        --timeout "$GUNICORN_TIMEOUT" \
        --access-logfile - \
        --error-logfile - \
        app:app &
    SERVER_PID=$!
fi

echo "  Server PID: $SERVER_PID"
echo "  Press Ctrl+C to stop"
echo "=========================================="

# Wait for the server process
wait $SERVER_PID 2>/dev/null
