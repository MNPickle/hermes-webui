#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCREENSHOT_DIR="${1:-/tmp/hermes-pw-capability-e2e}"
PORT="${2:-5058}"
shift $(( $# > 0 ? 1 : 0 ))
shift $(( $# > 0 ? 1 : 0 ))

KEEP_HOME=0
PASSTHRU_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--keep-home" ]]; then
    KEEP_HOME=1
  else
    PASSTHRU_ARGS+=("$arg")
  fi
done

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi

if [[ -z "${HERMES_WEBUI_TOKEN:-}" ]]; then
  echo "HERMES_WEBUI_TOKEN is not set. Add it to $ROOT_DIR/.env or export it before running." >&2
  exit 1
fi

CALLER_HOME="${HOME}"
WEBUI_VENV="${WEBUI_VENV:-$CALLER_HOME/.hermes/.venv}"
PLAYWRIGHT_PYTHON="$WEBUI_VENV/bin/python"

if [[ ! -x "$PLAYWRIGHT_PYTHON" ]]; then
  echo "Python runtime not found at $PLAYWRIGHT_PYTHON" >&2
  echo "Set WEBUI_VENV to the Hermes/web UI virtualenv and try again." >&2
  exit 1
fi

export TMPDIR=/tmp
export TMP=/tmp
export TEMP=/tmp
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$CALLER_HOME/.cache/ms-playwright}"

if ! "$PLAYWRIGHT_PYTHON" - <<'PY' >/dev/null 2>&1
import os
from pathlib import Path

browser_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
patterns = (
    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium-*/chrome-linux/chrome",
)
found = any(any(browser_root.glob(pattern)) for pattern in patterns)
raise SystemExit(0 if found else 1)
PY
then
  echo "Installing Playwright Chromium into $PLAYWRIGHT_BROWSERS_PATH ..." >&2
  "$PLAYWRIGHT_PYTHON" -m playwright install chromium >&2
fi

TMP_HOME="$(mktemp -d /tmp/hermes-cap-e2e-XXXXXX)"
mkdir -p "$TMP_HOME/.hermes"
URL="http://127.0.0.1:$PORT/"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    local children
    children="$(pgrep -P "$SERVER_PID" 2>/dev/null || true)"
    if [[ -n "$children" ]]; then
      kill $children >/dev/null 2>&1 || true
      wait $children >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ "$KEEP_HOME" -eq 0 ]]; then
    rm -rf "$TMP_HOME"
  else
    echo "Kept temporary Hermes home at $TMP_HOME" >&2
  fi
}
trap cleanup EXIT

HOME="$TMP_HOME" WEBUI_VENV="$WEBUI_VENV" DEV=1 "$ROOT_DIR/start.sh" "$PORT" &
SERVER_PID=$!

AUTH_HEADER="Authorization: Bearer $HERMES_WEBUI_TOKEN"

for _ in $(seq 1 90); do
  if curl -fsS -H "$AUTH_HEADER" "$URL/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS -H "$AUTH_HEADER" "$URL/api/health" >/dev/null 2>&1; then
  echo "Timed out waiting for isolated Hermes Web UI on $URL" >&2
  exit 1
fi

set +e
"$PLAYWRIGHT_PYTHON" \
  "$ROOT_DIR/tools/playwright_capability_e2e.py" \
  --url "$URL" \
  --token "$HERMES_WEBUI_TOKEN" \
  --hermes-home "$TMP_HOME" \
  --screenshot-dir "$SCREENSHOT_DIR" \
  "${PASSTHRU_ARGS[@]}"
STATUS=$?
set -e
exit "$STATUS"
