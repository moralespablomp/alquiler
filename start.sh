#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creando entorno virtual..."
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --disable-pip-version-check -r requirements.txt
python server.py
