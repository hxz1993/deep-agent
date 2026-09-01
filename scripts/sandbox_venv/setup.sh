#!/usr/bin/env bash
# Create or refresh the independent subprocess-dev sandbox venv.
# Usage (from repo root):
#   bash scripts/sandbox_venv/setup.sh
#   bash scripts/sandbox_venv/setup.sh /path/to/python3
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${DEEP_AGENT_SANDBOX_VENV_DIR:-$ROOT/.venv-sandbox}"
REQ_FILE="$ROOT/scripts/sandbox_venv/requirements.txt"
# Keep in sync with deep_agent/options.py DEFAULT_SANDBOX_PIP_* .
DEEP_AGENT_SANDBOX_PIP_INDEX_URL="${DEEP_AGENT_SANDBOX_PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST="${DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"

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

PIP_EXTRA_ARGS=()
if [[ -n "${DEEP_AGENT_SANDBOX_PIP_INDEX_URL:-}" ]]; then
  PIP_EXTRA_ARGS+=(-i "$DEEP_AGENT_SANDBOX_PIP_INDEX_URL")
  if [[ -n "${DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST:-}" ]]; then
    PIP_EXTRA_ARGS+=(--trusted-host "$DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST")
  fi
  echo "Using pip index: ${DEEP_AGENT_SANDBOX_PIP_INDEX_URL}"
fi

python -m pip install "${PIP_EXTRA_ARGS[@]}" --upgrade pip
python -m pip install "${PIP_EXTRA_ARGS[@]}" -r "$REQ_FILE"

python - <<'PY'
import numpy
import pandas
import pydantic
import requests
import httpx
import bs4
import lxml
import PIL
import matplotlib
import pypdf
import pdfplumber
import reportlab
import openpyxl
import xlsxwriter
import docx
import pptx

print("sandbox packages ok:")
print(f"  numpy={numpy.__version__}")
print(f"  pandas={pandas.__version__}")
print(f"  pydantic={pydantic.__version__}")
print("  web/image/pdf/office packages=ok")
assert tuple(int(x) for x in numpy.__version__.split(".")[:2]) < (2, 0), numpy.__version__
PY

echo "Sandbox interpreter: $VENV_DIR/bin/python"
echo "Set DEEP_AGENT_SANDBOX_PYTHON=$VENV_DIR/bin/python (default resolves to .venv-sandbox/bin/python)"
