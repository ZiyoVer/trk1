#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Avval ./setup.sh ni ishga tushiring." >&2
  exit 1
fi

if [[ "$(uname -m)" == "arm64" ]] || [[ "$(sysctl -in hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then
  exec arch -arm64 .venv/bin/python translator.py "$@"
fi

exec .venv/bin/python translator.py "$@"

