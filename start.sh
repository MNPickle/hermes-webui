#!/bin/bash
# ── Hermes Web UI Launcher ──────────────────────────────
# Usage: ./start.sh          (starts on port 5000)
#        ./start.sh 8080     (starts on custom port)
#        DEV=1 ./start.sh    (use Flask dev server)

PORT="${1:-${PORT:-5000}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"

detect_webui_venv() {
    local candidate
    if [ -n "${WEBUI_VENV:-}" ]; then
        echo "$WEBUI_VENV"
        return
    fi
    for candidate in "$APP_DIR/.venv" "$HOME/.hermes/.venv"; do
        if [ -x "$candidate/bin/python" ]; then
            echo "$candidate"
            return
        fi
    done
    echo "$APP_DIR/.venv"
}

WEBUI_VENV="$(detect_webui_venv)"
PYTHON_BIN="$WEBUI_VENV/bin/python"
FLASK_BIN="$WEBUI_VENV/bin/flask"
GUNICORN_BIN="$WEBUI_VENV/bin/gunicorn"

if [ -z "${HERMES_WEBUI_HERMES_BIN:-}" ] && [ -x "$HOME/.hermes/hermes-agent/venv/bin/hermes" ]; then
    export HERMES_WEBUI_HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes"
fi

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

if [ -n "${HERMES_WEBUI_HERMES_BIN:-}" ]; then
    echo "  Hermes CLI: $HERMES_WEBUI_HERMES_BIN"
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
    GUNICORN_TIMEOUT_HEADROOM="${GUNICORN_TIMEOUT_HEADROOM:-90}"
    GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-$((CHAT_TIMEOUT + GUNICORN_TIMEOUT_HEADROOM))}"
    GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
    GUNICORN_GRACEFUL_TIMEOUT="${GUNICORN_GRACEFUL_TIMEOUT:-30}"
    GUNICORN_KEEPALIVE="${GUNICORN_KEEPALIVE:-10}"
    GUNICORN_MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-200}"
    GUNICORN_MAX_REQUESTS_JITTER="${GUNICORN_MAX_REQUESTS_JITTER:-25}"
    GUNICORN_LOG_LEVEL="${GUNICORN_LOG_LEVEL:-info}"
    GUNICORN_ACCESS_LOG_FORMAT="${GUNICORN_ACCESS_LOG_FORMAT:-%(h)s pid=%(p)s [%(t)s] \"%(r)s\" %(s)s %(B)s dur_ms=%(M)s ref=\"%(f)s\" ua=\"%(a)s\"}"
    GUNICORN_CONFIG="${GUNICORN_CONFIG:-$APP_DIR/gunicorn.conf.py}"
    export CHAT_TIMEOUT GUNICORN_TIMEOUT_HEADROOM GUNICORN_TIMEOUT GUNICORN_WORKERS \
        GUNICORN_GRACEFUL_TIMEOUT GUNICORN_KEEPALIVE GUNICORN_MAX_REQUESTS \
        GUNICORN_MAX_REQUESTS_JITTER GUNICORN_LOG_LEVEL
    echo "  workers=$GUNICORN_WORKERS timeout=${GUNICORN_TIMEOUT}s (chat=${CHAT_TIMEOUT}s + headroom=${GUNICORN_TIMEOUT_HEADROOM}s)"
    echo "  graceful_timeout=${GUNICORN_GRACEFUL_TIMEOUT}s keepalive=${GUNICORN_KEEPALIVE}s max_requests=${GUNICORN_MAX_REQUESTS}+${GUNICORN_MAX_REQUESTS_JITTER}"
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
        --config "$GUNICORN_CONFIG" \
        --bind "127.0.0.1:$PORT" \
        --workers "$GUNICORN_WORKERS" \
        --chdir "$APP_DIR" \
        --worker-tmp-dir "$GUNICORN_WORKER_TMP" \
        --timeout "$GUNICORN_TIMEOUT" \
        --graceful-timeout "$GUNICORN_GRACEFUL_TIMEOUT" \
        --keep-alive "$GUNICORN_KEEPALIVE" \
        --max-requests "$GUNICORN_MAX_REQUESTS" \
        --max-requests-jitter "$GUNICORN_MAX_REQUESTS_JITTER" \
        --log-level "$GUNICORN_LOG_LEVEL" \
        --access-logfile - \
        --access-logformat "$GUNICORN_ACCESS_LOG_FORMAT" \
        --error-logfile - \
        app:app &
    SERVER_PID=$!
fi

echo "  Server PID: $SERVER_PID"
echo "  Press Ctrl+C to stop"
echo "=========================================="

# Wait for the server process
wait $SERVER_PID 2>/dev/null
