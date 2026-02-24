#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v poetry >/dev/null 2>&1; then
  exec poetry run esprit "$@"
fi

for local_esprit in "$SCRIPT_DIR"/.venv*/bin/esprit "$SCRIPT_DIR"/.venv/bin/esprit; do
  if [ -x "$local_esprit" ]; then
    exec "$local_esprit" "$@"
  fi
done

if command -v esprit >/dev/null 2>&1; then
  exec esprit "$@"
fi

echo "Unable to start Esprit."
echo "Install Poetry and run 'poetry install', or install the 'esprit' CLI globally."
exit 1
