#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  ./setup.sh
fi

if [[ "$(uname -m)" == "arm64" ]]; then
  exec arch -arm64 .venv/bin/python overlay.py
else
  exec .venv/bin/python overlay.py
fi
