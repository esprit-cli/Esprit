#!/usr/bin/env bash

set -euo pipefail

APP="esprit"
REPO_URL="${ESPRIT_REPO_URL:-https://github.com/improdead/Esprit.git}"
REPO_REF="${ESPRIT_REPO_REF:-main}"
INSTALL_ROOT="${ESPRIT_HOME:-$HOME/.esprit}"
BIN_DIR="$INSTALL_ROOT/bin"
RUNTIME_DIR="$INSTALL_ROOT/runtime"
VENV_DIR="$INSTALL_ROOT/venv"
LAUNCHER_PATH="$BIN_DIR/$APP"
ESPRIT_IMAGE="${ESPRIT_IMAGE:-improdead/esprit-sandbox:latest}"

MUTED='\033[0;2m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

FORCE=false
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=true ;;
  esac
done

print_message() {
  local level="$1"
  local message="$2"
  local color="$NC"
  case "$level" in
    success) color="$GREEN" ;;
    warning) color="$YELLOW" ;;
    error) color="$RED" ;;
  esac
  echo -e "${color}${message}${NC}"
}

require_command() {
  local cmd="$1"
  local install_hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    print_message error "Missing required command: $cmd"
    print_message info "$install_hint"
    exit 1
  fi
}

verify_signature() {
    if ! command -v gpg &> /dev/null; then
        echo "⚠ gpg not found — skipping signature verification"
        return 0
    fi

    # Import the Esprit public key
    local key_url="https://raw.githubusercontent.com/improdead/Esprit/main/keys/esprit-release.pub"
    if curl -fsSL "$key_url" | gpg --import 2>/dev/null; then
        echo "✓ Esprit release key imported"
    else
        echo "⚠ Could not import release key — skipping verification"
        return 0
    fi

    # Verify if a signature file exists
    if [ -f "$1.sig" ]; then
        if gpg --verify "$1.sig" "$1" 2>/dev/null; then
            echo "✓ Signature verified"
        else
            echo "⚠ Signature verification failed"
            echo "  The download may have been tampered with."
            echo "  Continue anyway? (y/N)"
            read -r response
            [ "$response" = "y" ] || exit 1
        fi
    fi
}

choose_python() {
  local candidate
  for candidate in python3.13 python3.12 python3; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi

    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
    then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

sync_runtime_repo() {
  print_message info "${MUTED}Syncing Esprit runtime source...${NC}"

  if [ -d "$RUNTIME_DIR/.git" ]; then
    git -C "$RUNTIME_DIR" remote set-url origin "$REPO_URL"
    git -C "$RUNTIME_DIR" fetch --depth 1 origin "$REPO_REF"
    git -C "$RUNTIME_DIR" checkout -q FETCH_HEAD
  else
    rm -rf "$RUNTIME_DIR"
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$RUNTIME_DIR"
  fi

  local runtime_commit
  runtime_commit=$(git -C "$RUNTIME_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
  print_message success "✓ Runtime ready (${runtime_commit})"
}

install_python_runtime() {
  local py_bin="$1"

  mkdir -p "$INSTALL_ROOT"
  if [ "$FORCE" = true ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    print_message info "${MUTED}Creating virtual environment...${NC}"
    rm -rf "$VENV_DIR"
    "$py_bin" -m venv "$VENV_DIR"
  fi

  print_message info "${MUTED}Installing Esprit dependencies (this can take a few minutes)...${NC}"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$VENV_DIR/bin/pip" install --upgrade "$RUNTIME_DIR" >/dev/null
  print_message success "✓ Python runtime installed"
}

write_launcher() {
  mkdir -p "$BIN_DIR"

  cat > "$LAUNCHER_PATH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT="${ESPRIT_HOME:-$HOME/.esprit}"
BIN="$ROOT/venv/bin/esprit"

if [ ! -x "$BIN" ]; then
  echo "Esprit runtime not found. Re-run the installer."
  exit 1
fi

exec "$BIN" "$@"
EOF

  chmod 755 "$LAUNCHER_PATH"
  print_message success "✓ Installed launcher at $LAUNCHER_PATH"
}

setup_path() {
  local shell_name
  shell_name=$(basename "${SHELL:-sh}")

  local config_file=""
  case "$shell_name" in
    zsh)
      config_file="${ZDOTDIR:-$HOME}/.zshrc"
      ;;
    bash)
      config_file="$HOME/.bashrc"
      ;;
    fish)
      config_file="$HOME/.config/fish/config.fish"
      ;;
    *)
      config_file="$HOME/.profile"
      ;;
  esac

  if [ "$shell_name" = "fish" ]; then
    local line="fish_add_path $BIN_DIR"
    if [ -f "$config_file" ] && grep -Fxq "$line" "$config_file" 2>/dev/null; then
      return
    fi
    if [ ! -f "$config_file" ]; then
      mkdir -p "$(dirname "$config_file")"
      touch "$config_file"
    fi
    echo "" >> "$config_file"
    echo "# esprit" >> "$config_file"
    echo "$line" >> "$config_file"
    print_message info "${MUTED}Added esprit to PATH in ${NC}$config_file"
    return
  fi

  local export_line="export PATH=$BIN_DIR:\$PATH"
  if [ -f "$config_file" ] && grep -Fxq "$export_line" "$config_file" 2>/dev/null; then
    return
  fi

  if [ ! -f "$config_file" ]; then
    touch "$config_file"
  fi
  if [ -w "$config_file" ]; then
    echo "" >> "$config_file"
    echo "# esprit" >> "$config_file"
    echo "$export_line" >> "$config_file"
    print_message info "${MUTED}Added esprit to PATH in ${NC}$config_file"
  else
    print_message warning "Could not update $config_file automatically."
    print_message info "Add this line manually: $export_line"
  fi
}

warm_docker_image() {
  if [ "${ESPRIT_SKIP_DOCKER_WARM:-0}" = "1" ]; then
    return
  fi

  if ! command -v docker >/dev/null 2>&1; then
    print_message warning "Docker not found (required for local/provider scans)."
    print_message info "Esprit Cloud scans still work without Docker."
    return
  fi

  if ! docker info >/dev/null 2>&1; then
    print_message warning "Docker daemon is not running."
    print_message info "Start Docker for local/provider scans."
    return
  fi

  print_message info "${MUTED}Pulling sandbox image (optional warm-up)...${NC}"
  local pull_output
  local pull_status=0
  pull_output="$(docker pull "$ESPRIT_IMAGE" 2>&1)" || pull_status=$?

  if [ "$pull_status" -eq 0 ]; then
    print_message success "✓ Sandbox image ready"
    return
  fi

  echo -e "$pull_output"

  if [[ "$(uname -m)" == "arm64" ]] && echo "$pull_output" | grep -qi "no matching manifest" && echo "$pull_output" | grep -qi "arm64"; then
    print_message warning "Native arm64 image missing; retrying with linux/amd64 emulation..."
    if docker pull --platform linux/amd64 "$ESPRIT_IMAGE" >/dev/null; then
      print_message success "✓ Sandbox image ready (linux/amd64 emulation)"
      return
    fi
  fi

  print_message warning "Sandbox pull skipped (will retry at first local scan)."
}

main() {
  require_command git "Install git and re-run the installer."
  require_command curl "Install curl and re-run the installer."

  local py_bin
  py_bin=$(choose_python || true)
  if [ -z "$py_bin" ]; then
    print_message error "Python 3.12+ is required."
    print_message info "Install Python 3.12 and re-run this installer."
    exit 1
  fi

  print_message info "${CYAN}Installing Esprit${NC} ${MUTED}(source mode)${NC}"
  print_message info "${MUTED}Runtime source:${NC} $REPO_URL@$REPO_REF"
  print_message info "${MUTED}Install root:${NC} $INSTALL_ROOT"

  sync_runtime_repo
  verify_signature "$RUNTIME_DIR"
  install_python_runtime "$py_bin"
  write_launcher
  setup_path
  warm_docker_image

  local version
  version=$("$LAUNCHER_PATH" --version 2>/dev/null || echo "unknown")
  print_message success "✓ ${version} ready"

  echo ""
  echo -e "${MUTED}Run this now (or open a new terminal):${NC}"
  echo -e "  ${MUTED}export PATH=$BIN_DIR:\$PATH${NC}"
  echo -e "  ${MUTED}$APP --help${NC}"
}

main
