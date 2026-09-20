#!/usr/bin/env sh
set -eu
PYTHONPATH=src python3 -m apc_pg3x.installer
python3 -m pip install --user -r requirements.txt
python3 -m pip install --user --no-deps .
