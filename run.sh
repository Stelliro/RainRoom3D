#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

if [ ! -f ".venv/.deps_ok" ]; then
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  echo ok > .venv/.deps_ok
fi

python -m app.main
