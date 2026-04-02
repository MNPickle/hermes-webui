#!/bin/bash
# ── Hermes Web UI Launcher ──────────────────────────────
# Usage: ./start.sh          (starts on port 5000)
#        ./start.sh 8080     (starts on custom port)
#        DEV=1 ./start.sh    (use Flask dev server)

PORT="${1:-5000}"
WEBUI_VENV="$HOME/.hermes/.venv"
APP_DIR="$HOME/hermes-web-ui"

echo "=========================================="
echo "  Hermes Agent Web UI"
echo "  http://127.0.0.1:$PORT"
echo "=========================================="
echo ""

cd "$APP_DIR" || exit 1

# Check if already running on this port
if curl -s "http://127.0.0.1:$PORT/" > /dev/null 2>&1; then
    echo "  Web UI is already running on port $PORT"
    echo "  Open http://127.0.0.1:$PORT in your browser"
    echo "=========================================="
    exit 0
fi

if [ -f "$APP_DIR/.env" ]; then
    set -a; . "$APP_DIR/.env"; set +a
fi

# Use gunicorn for production, Flask dev server only if DEV=1
if [ "${DEV}" = "1" ]; then
    echo "  [DEV MODE] Using Flask development server"
    FLASK_APP="$APP_DIR/app.py" "$WEBUI_VENV/bin/flask" run --host 127.0.0.1 --port "$PORT" &
    SERVER_PID=$!
else
    echo "  [PRODUCTION] Using gunicorn"
    GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-330}"
    "$WEBUI_VENV/bin/gunicorn" \
        --bind "127.0.0.1:$PORT" \
        --workers 2 \
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
