#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MUTED='\033[0;2m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

_debug_start() {
  if [ "${ESPRIT_DEBUG_START:-0}" = "1" ]; then
    echo -e "${MUTED}[launcher] $*${NC}"
  fi
}

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
for local_python in "$SCRIPT_DIR"/.venv*/bin/python "$SCRIPT_DIR"/.venv/bin/python; do
  if [ -x "$local_python" ]; then
    if PYTHONPATH="$SCRIPT_DIR" "$local_python" -c "import esprit.interface.main" >/dev/null 2>&1; then
      _debug_start "using local venv python: $local_python"
      exec env PYTHONPATH="$SCRIPT_DIR" "$local_python" -m esprit.interface.main "$@"
    fi
  fi
done

# ── Local source (installed deps, no Poetry) path ───────────────────
# Prefer running repo source when Python deps are already present.
if [ -f pyproject.toml ]; then
  for py in python3.12 python3.11; do
    if command -v "$py" >/dev/null 2>&1; then
      if PYTHONPATH="$SCRIPT_DIR" "$py" -c "import esprit.interface.main" >/dev/null 2>&1; then
        _debug_start "using local source python: $py"
        exec env PYTHONPATH="$SCRIPT_DIR" "$py" -m esprit.interface.main "$@"
      fi
    fi
  done
fi

# ── Node.js (npm install) path ───────────────────────────────────────
if command -v node >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/bin/esprit.js" ]; then
  if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    echo -e "${YELLOW}Using npm binary fallback (local Python environment unavailable).${NC}"
  fi
  _debug_start "using npm bootstrap binary"
  exec node "$SCRIPT_DIR/bin/esprit.js" "$@"
fi

# ── Binary path ──────────────────────────────────────────────────────
if command -v esprit >/dev/null 2>&1; then
  # Check for updates in the background (non-blocking)
  _check_update() {
    _version_is_newer() {
      local installed_v="${1#v}"
      local latest_v="${2#v}"
      local IFS=.
      local installed_parts latest_parts
      read -r -a installed_parts <<<"$installed_v"
      read -r -a latest_parts <<<"$latest_v"

      local max_len="${#installed_parts[@]}"
      if [ "${#latest_parts[@]}" -gt "$max_len" ]; then
        max_len="${#latest_parts[@]}"
      fi

      local i installed_num latest_num
      for ((i = 0; i < max_len; i++)); do
        installed_num="${installed_parts[i]:-0}"
        latest_num="${latest_parts[i]:-0}"
        installed_num="${installed_num%%[^0-9]*}"
        latest_num="${latest_num%%[^0-9]*}"
        [ -z "$installed_num" ] && installed_num=0
        [ -z "$latest_num" ] && latest_num=0

        if ((latest_num > installed_num)); then
          return 0
        fi
        if ((latest_num < installed_num)); then
          return 1
        fi
      done

      return 1
    }

    local repo="improdead/Esprit"
    local installed
    installed=$(esprit --version 2>/dev/null | awk '{print $2}' || echo "")
    [ -z "$installed" ] && return

    local latest
    latest=$(curl -sf --max-time 3 \
      "https://api.github.com/repos/$repo/releases/latest" \
      | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' 2>/dev/null || echo "")
    [ -z "$latest" ] && return

    if _version_is_newer "$installed" "$latest"; then
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
