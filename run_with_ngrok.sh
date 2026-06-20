#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
NGROK_LOG="$ROOT_DIR/voicebot/logs/ngrok.log"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing virtualenv at $ROOT_DIR/.venv"
  echo "Create it first with: python3 -m venv .venv"
  exit 1
fi

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok is not installed."
  echo "Install it first with: brew install ngrok"
  echo "Then connect your account with: ngrok config add-authtoken <your-token>"
  exit 1
fi

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"
NGROK_REGION="${NGROK_REGION:-us}"
NGROK_API_URL="${NGROK_API_URL:-http://127.0.0.1:4040/api/tunnels}"
export NGROK_API_URL

cleanup() {
  if [[ -n "${NGROK_PID:-}" ]]; then
    kill "$NGROK_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

mkdir -p "$ROOT_DIR/voicebot/logs"

ngrok http "$APP_PORT" --log=stdout --region="$NGROK_REGION" >"$NGROK_LOG" 2>&1 &
NGROK_PID=$!

PUBLIC_BASE_URL="$("$VENV_PYTHON" - <<'PY'
import json
import time
import urllib.request
import os
import sys

api_url = os.environ["NGROK_API_URL"]
deadline = time.time() + 20

while time.time() < deadline:
    try:
        with urllib.request.urlopen(api_url, timeout=2) as response:
            payload = json.load(response)
        tunnels = payload.get("tunnels", [])
        https_tunnels = [item for item in tunnels if item.get("public_url", "").startswith("https://")]
        if https_tunnels:
            print(https_tunnels[0]["public_url"])
            sys.exit(0)
    except Exception:
        time.sleep(0.5)

print("Timed out waiting for ngrok to publish an https tunnel.", file=sys.stderr)
sys.exit(1)
PY
)"

export PUBLIC_BASE_URL

echo "ngrok tunnel ready: $PUBLIC_BASE_URL"
echo "dashboard: $PUBLIC_BASE_URL/"
echo "local dashboard: http://127.0.0.1:$APP_PORT/"
echo "ngrok log: $NGROK_LOG"

exec "$ROOT_DIR/run.sh"
