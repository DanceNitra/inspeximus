#!/usr/bin/env bash
# Build one venv per version and print the whole before/after matrix. No repo checkout needed.
set -u
VERSIONS="${*:-1.67.0 1.68.0 1.71.0 1.72.0}"
for V in $VERSIONS; do
  D=".venv-$V"
  python -m venv "$D" >/dev/null 2>&1
  PIP="$D/bin/pip";  [ -x "$PIP" ] || PIP="$D/Scripts/pip"
  PY="$D/bin/python"; [ -x "$PY" ]  || PY="$D/Scripts/python"
  "$PIP" install -q --no-cache-dir "inspeximus==$V" 2>/dev/null
  echo "===== inspeximus $V ====="
  "$PY" reproduce.py 2>&1 | grep '^\['
  echo
done
