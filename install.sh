#!/usr/bin/env sh
set -eu
python3 -m pip install --user -r requirements.txt
python3 -m pip install --user --no-deps .
mkdir -p data
chmod 700 data
if [ -f data/apc-bootstrap.json ]; then
  chmod 600 data/apc-bootstrap.json
fi
