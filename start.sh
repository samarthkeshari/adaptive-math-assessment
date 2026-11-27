#!/usr/bin/env bash
# Start the Adaptive Math Assessment Agent locally.
#
#   ./start.sh            auto: gateway if a token helper is configured, otherwise direct API
#   ./start.sh direct     Claude via the Anthropic API with ANTHROPIC_API_KEY
#   ./start.sh gateway    Claude via a company AI gateway with a token helper
#
# Settings come from the environment and/or a .env file next to this script (template in
# README). Non-empty variables already exported in your shell take precedence over .env.
# ENV_FILE=other.env ./start.sh   reads a different settings file.
#   PORT=9000 ./start.sh direct     custom port
#
# Python dependencies live in a virtual environment (.venv) managed by ./setup.sh,
# which this script runs first.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-auto}"
case "$MODE" in auto|direct|gateway) ;; *)
  echo "Usage: ./start.sh [direct|gateway]" >&2; exit 2 ;;
esac

# ---- load .env: KEY=value lines. Shell variables win unless they are empty.
# Reports which setting NAMES were loaded (never values) so problems are easy to spot.
ENV_FILE="${ENV_FILE:-.env}"
ENV_LOADED=""
if [ -f "$ENV_FILE" ]; then
  kept=""; bad=""; n=0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line="${line%$'\r'}"                                    # Windows line endings
    [ "$n" -eq 1 ] && line="${line#$'\xef\xbb\xbf'}"        # UTF-8 byte-order mark
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    if ! [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      bad="$bad $n"; continue
    fi
    key="${BASH_REMATCH[2]}"; value="${BASH_REMATCH[3]}"
    if [[ "$value" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ || "$value" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
      value="${BASH_REMATCH[1]}"                            # quoted value (may contain #)
    else
      value="${value%%[[:space:]]#*}"                       # drop "  # trailing comment"
      value="${value%"${value##*[![:space:]]}"}"            # trim trailing spaces
    fi
    if [ -n "${!key:-}" ]; then
      kept="$kept $key"                                     # already set (non-empty) in your shell
    else
      export "$key=$value"
      [ -n "$value" ] && ENV_LOADED="$ENV_LOADED $key"
    fi
  done < "$ENV_FILE"
  echo "Loaded from $ENV_FILE:${ENV_LOADED:- (no settings)}"
  [ -n "$kept" ] && echo "Using your shell's values instead of $ENV_FILE for:$kept"
  [ -n "$bad" ] && echo "Warning: couldn't read line(s)$bad of $ENV_FILE (expected KEY=value)" >&2
else
  echo "No $ENV_FILE file in $(pwd)."
  if [[ "$ENV_FILE" != */* ]]; then                         # e.g. saved as ".env.txt" or "env"
    others="$(ls -a | grep -iE '^\.?env' | grep -vxF "$ENV_FILE" | tr '\n' ' ' || true)"
    [ -n "$others" ] && echo "  Found instead: $others(it must be named exactly $ENV_FILE)"
  fi
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
      echo "Direct mode needs ANTHROPIC_API_KEY (in $ENV_FILE or exported)." >&2
      similar="$(printf '%s\n' $ENV_LOADED | grep -iE 'anthropic|claude|api_?key' | grep -vx ANTHROPIC_API_KEY | tr '\n' ' ' || true)"
      [ -n "$similar" ] && echo "  $ENV_FILE has: $similar- the name must be exactly ANTHROPIC_API_KEY" >&2
      echo "  Expected a line like:  ANTHROPIC_API_KEY=sk-ant-..." >&2
      echo "  Get a key at https://console.anthropic.com/ or use: ./start.sh gateway" >&2
      exit 1
    fi
    echo "Claude connection: direct Anthropic API"
  fi
else
  echo "LLM provider: ${LLM_PROVIDER} (${LLM_MODEL:-model not set})"
fi

# ---- virtual environment: create/update .venv (see setup.sh), then run inside it
./setup.sh
VENV_DIR="${VENV_DIR:-.venv}"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

PORT="${PORT:-8000}"
echo "Student data is saved in ${DATA_DIR:-$(pwd)/data}/students/<student-name>/"
echo "Open http://127.0.0.1:${PORT} in your browser (Ctrl+C to stop)."
# Backend API (backend/) also serves the static frontend (frontend/).
exec uvicorn backend.main:app --host 127.0.0.1 --port "$PORT"
