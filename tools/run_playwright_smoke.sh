#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCREENSHOT_DIR="${1:-/tmp/hermes-pw-smoke}"
URL="${2:-http://127.0.0.1:5057/}"
shift $(( $# > 0 ? 1 : 0 ))
shift $(( $# > 0 ? 1 : 0 ))

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi

if [[ -z "${HERMES_WEBUI_TOKEN:-}" ]]; then
  echo "HERMES_WEBUI_TOKEN is not set. Add it to $ROOT_DIR/.env or export it before running." >&2
  exit 1
fi

export TMPDIR=/tmp
export TMP=/tmp
export TEMP=/tmp
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

if ! "$HOME/.hermes/.venv/bin/python" - <<'PY' >/dev/null 2>&1
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
  "$HOME/.hermes/.venv/bin/python" -m playwright install chromium >&2
fi

exec "$HOME/.hermes/.venv/bin/python" \
  "$ROOT_DIR/tools/playwright_smoke.py" \
  --url "$URL" \
  --token "$HERMES_WEBUI_TOKEN" \
  --screenshot-dir "$SCREENSHOT_DIR" \
  "$@"
