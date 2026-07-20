#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="$(command -v python3)"
if [[ "$(uname -m)" == "arm64" ]] || [[ "$(sysctl -in hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then
  arch -arm64 "$PYTHON_BIN" -m venv .venv
  arch -arm64 .venv/bin/python -m pip install --upgrade pip
  arch -arm64 .venv/bin/python -m pip install -r requirements.txt
else
  "$PYTHON_BIN" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "Tayyor. Qurilmalarni tekshirish: ./run.sh --list-devices"

