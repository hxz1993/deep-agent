#!/usr/bin/env bash
# Create or refresh the independent subprocess-dev sandbox venv.
# Usage (from repo root):
#   bash scripts/sandbox_venv/setup.sh
#   bash scripts/sandbox_venv/setup.sh /path/to/python3
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${DEEP_AGENT_SANDBOX_VENV_DIR:-$ROOT/.venv-sandbox}"
REQ_FILE="$ROOT/scripts/sandbox_venv/requirements.txt"

if [[ $# -ge 1 ]]; then
  PYTHON_BIN="$1"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "error: no Python interpreter found" >&2
  exit 1
fi

echo "Creating sandbox venv at $VENV_DIR using $PYTHON_BIN"
"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$REQ_FILE"

python - <<'PY'
import numpy
import pandas
import pydantic

print("sandbox packages ok:")
print(f"  numpy={numpy.__version__}")
print(f"  pandas={pandas.__version__}")
print(f"  pydantic={pydantic.__version__}")
assert tuple(int(x) for x in numpy.__version__.split(".")[:2]) < (2, 0), numpy.__version__
PY

echo "Sandbox interpreter: $VENV_DIR/bin/python"
echo "Set DEEP_AGENT_SANDBOX_PYTHON=$VENV_DIR/bin/python (default resolves to .venv-sandbox/bin/python)"
