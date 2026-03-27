#!/bin/bash
# ── Hermes Web UI Launcher ──────────────────────────────
# Usage: ./start.sh          (starts on port 5000)
#        ./start.sh 8080     (starts on custom port)
#        DEV=1 ./start.sh    (use Flask dev server)

PORT="${1:-5000}"
HERMES_VENV="$HOME/.hermes/hermes-agent/venv"
WEBUI_VENV="$HOME/.hermes/.venv"
APP_DIR="$HOME/hermes-web-ui"

echo "=========================================="
echo "  Hermes Agent Web UI"
echo "  http://127.0.0.1:$PORT"
echo "=========================================="
echo ""

cd "$APP_DIR" || exit 1

# Check if already running on this port
if curl -s "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "  Web UI is already running on port $PORT"
    echo "  Open http://127.0.0.1:$PORT in your browser"
    echo "=========================================="
    exit 0
fi

# Use gunicorn for production, Flask dev server only if DEV=1
if [ "${DEV}" = "1" ]; then
    echo "  [DEV MODE] Using Flask development server"
    FLASK_PORT="$PORT" "$WEBUI_VENV/bin/python" "$APP_DIR/app.py" &
    SERVER_PID=$!
else
    echo "  [PRODUCTION] Using gunicorn"
    "$WEBUI_VENV/bin/gunicorn" \
        --bind "127.0.0.1:$PORT" \
        --workers 1 \
        --timeout 120 \
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
