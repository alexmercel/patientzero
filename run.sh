#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing virtualenv at $ROOT_DIR/.venv"
  echo "Create it first with: python3 -m venv .venv"
  exit 1
fi

preserve_vars=(
  PUBLIC_BASE_URL
  APP_HOST
  APP_PORT
  LOG_LEVEL
  GEMINI_MODEL
)

for var_name in "${preserve_vars[@]}"; do
  if [[ -n "${!var_name+x}" ]]; then
    export "__VOICEBOT_ORIG_${var_name}=${!var_name}"
  fi
done

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

for var_name in "${preserve_vars[@]}"; do
  original_name="__VOICEBOT_ORIG_${var_name}"
  if [[ -n "${!original_name+x}" ]]; then
    export "$var_name=${!original_name}"
    unset "$original_name"
  fi
done

required_vars=(
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_PHONE_NUMBER
  MY_PHONE_NUMBER
  GEMINI_API_KEY
)

missing_vars=()
for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    missing_vars+=("$var_name")
  fi
done

if (( ${#missing_vars[@]} > 0 )); then
  echo "Missing required environment variables:"
  printf ' - %s\n' "${missing_vars[@]}"
  exit 1
fi

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"

echo "Starting voicebot with:"
echo " - APP_HOST=$APP_HOST"
echo " - APP_PORT=$APP_PORT"
echo " - PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-<unset>}"

exec "$VENV_PYTHON" -m uvicorn \
  voicebot.src.api.server:create_app \
  --factory \
  --host "$APP_HOST" \
  --port "$APP_PORT"
