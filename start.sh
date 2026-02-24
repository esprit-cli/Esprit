#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MUTED='\033[0;2m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Poetry (development / from-source) path ──────────────────────────
if command -v poetry >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  # Sync dependencies — catches missing/outdated packages (fast no-op when clean)
  echo -e "${MUTED}Syncing dependencies...${NC}"
  poetry install --no-interaction --quiet 2>/dev/null && \
    echo -e "${GREEN}✓${NC} ${MUTED}Dependencies up to date${NC}" || \
    echo -e "${YELLOW}⚠${NC} ${MUTED}poetry install had warnings (continuing anyway)${NC}"

  exec poetry run esprit "$@"
fi

# ── Local venv path ──────────────────────────────────────────────────
for local_esprit in "$SCRIPT_DIR"/.venv*/bin/esprit "$SCRIPT_DIR"/.venv/bin/esprit; do
  if [ -x "$local_esprit" ]; then
    exec "$local_esprit" "$@"
  fi
done

# ── Node.js (npm install) path ───────────────────────────────────────
if command -v node >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/bin/esprit.js" ]; then
  exec node "$SCRIPT_DIR/bin/esprit.js" "$@"
fi

# ── Binary path ──────────────────────────────────────────────────────
if command -v esprit >/dev/null 2>&1; then
  # Check for updates in the background (non-blocking)
  _check_update() {
    local repo="improdead/Esprit"
    local installed
    installed=$(esprit --version 2>/dev/null | awk '{print $2}' || echo "")
    [ -z "$installed" ] && return

    local latest
    latest=$(curl -sf --max-time 3 \
      "https://api.github.com/repos/$repo/releases/latest" \
      | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' 2>/dev/null || echo "")
    [ -z "$latest" ] && return

    if [ "$installed" != "$latest" ]; then
      echo -e "${YELLOW}Update available:${NC} $installed → ${GREEN}$latest${NC}"
      echo -e "${MUTED}Run:${NC} curl -fsSL https://raw.githubusercontent.com/$repo/refs/heads/main/scripts/install.sh | bash"
      echo ""
    fi
  }
  _check_update

  exec esprit "$@"
fi

echo "Unable to start Esprit."
echo "Install Poetry and run 'poetry install', or install the 'esprit' CLI globally."
exit 1
