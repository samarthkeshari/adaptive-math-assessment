#!/usr/bin/env bash
# Create or update the project's Python virtual environment with the backend dependencies.
#
#   ./setup.sh            create .venv if it's missing; (re)install dependencies only when
#                         backend/requirements.txt has changed since the last install
#   ./setup.sh --force    reinstall all dependencies
#
#   PYTHON=python3.12 ./setup.sh    choose the Python used to create the venv (3.10+)
#   VENV_DIR=/path/to/venv ./setup.sh
#
# ./start.sh runs this automatically, so you only need it to set up ahead of time.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
REQUIREMENTS="backend/requirements.txt"
STAMP="$VENV_DIR/.requirements.sha256"     # fingerprint of the last installed requirements
FORCE="${1:-}"

version_ok() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; }

fingerprint() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1   # macOS
  else sha256sum "$1" | cut -d' ' -f1; fi                                            # Linux
}

# ---- create the virtual environment (or rebuild one made with an unsupported Python)
if [ -x "$VENV_DIR/bin/python" ] && ! version_ok "$VENV_DIR/bin/python"; then
  echo "Existing $VENV_DIR uses $("$VENV_DIR/bin/python" --version 2>&1); rebuilding with Python 3.10+ ..."
  rm -rf "$VENV_DIR"
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    found="no '$PYTHON' on PATH"
  elif ! version_ok "$PYTHON"; then
    found="$("$PYTHON" --version 2>&1 || true)"
    found="${found:-"'$PYTHON' (unknown version)"}"
  fi
  if [ -n "${found:-}" ]; then
    echo "Python 3.10+ is required to create the virtual environment (found: $found)." >&2
    echo "Install Python 3.10+ or point to one: PYTHON=python3.12 ./setup.sh" >&2
    exit 1
  fi
  echo "Creating virtual environment in $VENV_DIR ($("$PYTHON" --version 2>&1)) ..."
  "$PYTHON" -m venv "$VENV_DIR"
  FORCE="--force"
fi

# ---- install dependencies when requirements changed (or on --force / a fresh venv)
current="$(fingerprint "$REQUIREMENTS")"
if [ "$FORCE" = "--force" ] || [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$current" ]; then
  echo "Installing dependencies from $REQUIREMENTS ..."
  "$VENV_DIR/bin/python" -m pip install -q --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -q -r "$REQUIREMENTS"
  echo "$current" > "$STAMP"
  echo "Virtual environment ready: $VENV_DIR"
else
  echo "Dependencies up to date ($VENV_DIR)."
fi
