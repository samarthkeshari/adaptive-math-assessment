#!/usr/bin/env bash
# Start the Adaptive Math Assessment Agent locally.
#
#   ./start.sh            auto: gateway if a token helper is configured, otherwise direct API
#   ./start.sh direct     Claude via the Anthropic API with ANTHROPIC_API_KEY
#   ./start.sh gateway    Claude via a company AI gateway with a token helper
#
# Settings come from the environment and/or a .env file next to this script (template in
# README). Variables already exported in your shell take precedence over .env.
#   PORT=9000 ./start.sh direct     custom port
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-auto}"
case "$MODE" in auto|direct|gateway) ;; *)
  echo "Usage: ./start.sh [direct|gateway]" >&2; exit 2 ;;
esac

# ---- load .env (simple KEY=VALUE lines; shell environment wins)
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"; value="${BASH_REMATCH[3]}"
    value="${value%"${value##*[![:space:]]}"}"                    # trim trailing spaces
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
    [ -z "${!key+x}" ] && export "$key=$value"
  done < .env
fi

# ---- pick the Claude connection
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-${ANTHROPIC_BASE_URL:-}}"
GATEWAY_TOKEN_HELPER="${GATEWAY_TOKEN_HELPER:-${ANTHROPIC_API_KEY_HELPER:-}}"
if [ "$MODE" = auto ]; then
  if [ -n "$GATEWAY_TOKEN_HELPER" ]; then MODE=gateway; else MODE=direct; fi
fi

if [ "${LLM_PROVIDER:-anthropic}" = "anthropic" ]; then
  if [ "$MODE" = gateway ]; then
    if [ -z "$GATEWAY_BASE_URL" ] || [ -z "$GATEWAY_TOKEN_HELPER" ]; then
      echo "Gateway mode needs GATEWAY_BASE_URL and GATEWAY_TOKEN_HELPER (in .env or exported)." >&2
      exit 1
    fi
    export ANTHROPIC_BASE_URL="$GATEWAY_BASE_URL"
    export ANTHROPIC_API_KEY_HELPER="$GATEWAY_TOKEN_HELPER"
    echo "Claude connection: gateway ${ANTHROPIC_BASE_URL} (token from helper)"
  else
    # Direct: make sure nothing routes the API key to a gateway by accident.
    unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY_HELPER
    if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
      echo "Direct mode needs ANTHROPIC_API_KEY (in .env or exported)." >&2
      echo "Get a key at https://console.anthropic.com/ or use: ./start.sh gateway" >&2
      exit 1
    fi
    echo "Claude connection: direct Anthropic API"
  fi
else
  echo "LLM provider: ${LLM_PROVIDER} (${LLM_MODEL:-model not set})"
fi

# ---- environment
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Python 3.10+ is required (found: $("$PYTHON" --version 2>&1))." >&2
  exit 1
fi
if [ ! -d .venv ]; then
  echo "Creating virtual environment in .venv ..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Installing dependencies ..."
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt

PORT="${PORT:-8000}"
echo "Student data is saved in ${DATA_DIR:-$(pwd)/data}/students/<student-name>/"
echo "Open http://127.0.0.1:${PORT} in your browser (Ctrl+C to stop)."
# Backend API (backend/) also serves the static frontend (frontend/).
exec uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
