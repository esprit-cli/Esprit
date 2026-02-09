#!/usr/bin/env bash
#
# Esprit CLI Installer (standalone version)
# For the hosted version, use: curl -fsSL https://esprit.dev/install.sh | bash
#

set -euo pipefail

APP="esprit"
REPO="junaid-mahmood/Esprit"

# Colors
DIM='\033[0;2m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

requested_version=${VERSION:-}
SKIP_DOWNLOAD=false

# Detect OS
raw_os=$(uname -s)
case "$raw_os" in
  Darwin*)  os="darwin" ;;
  Linux*)   os="linux" ;;
  MINGW*|MSYS*|CYGWIN*) os="windows" ;;
  *)        echo -e "${RED}Unsupported OS: $raw_os${NC}"; exit 1 ;;
esac

# Detect arch
arch=$(uname -m)
case "$arch" in
  aarch64)  arch="arm64" ;;
  x86_64)   arch="x86_64" ;;
  arm64)    arch="arm64" ;;
esac

# Rosetta detection
if [ "$os" = "darwin" ] && [ "$arch" = "x86_64" ]; then
  rosetta_flag=$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)
  if [ "$rosetta_flag" = "1" ]; then
    arch="arm64"
  fi
fi

target="$os-$arch"
case "$target" in
  linux-x86_64|darwin-x86_64|darwin-arm64) ;;
  *) echo -e "${RED}Unsupported platform: $target${NC}"; exit 1 ;;
esac

archive_ext=".tar.gz"

# Check dependencies
for cmd in curl tar; do
  if ! command -v $cmd >/dev/null 2>&1; then
    echo -e "${RED}Error: '$cmd' is required but not installed.${NC}"
    exit 1
  fi
done

INSTALL_DIR="$HOME/.esprit/bin"
mkdir -p "$INSTALL_DIR"

# Get version
if [ -z "$requested_version" ]; then
  specific_version=$(curl -s "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')
  if [[ $? -ne 0 || -z "$specific_version" ]]; then
    echo -e "${RED}Failed to fetch latest version${NC}"
    exit 1
  fi
else
  specific_version=$requested_version
fi

filename="$APP-${target}${archive_ext}"
url="https://github.com/$REPO/releases/download/v${specific_version}/$filename"

check_existing() {
  local found_paths=()
  while IFS= read -r -d '' path; do
    found_paths+=("$path")
  done < <(which -a esprit 2>/dev/null | tr '\n' '\0' || true)

  for path in "${found_paths[@]}"; do
    [[ ! -e "$path" || "$path" == "$INSTALL_DIR/esprit"* ]] && continue
    echo -e "${DIM}Found existing esprit at: ${NC}$path"
    if [[ "$path" == *".local/bin"* ]]; then
      command -v pipx >/dev/null 2>&1 && pipx uninstall esprit-cli 2>/dev/null || true
      rm -f "$path" 2>/dev/null || true
    elif [[ -L "$path" || -f "$path" ]]; then
      rm -f "$path" 2>/dev/null || true
    fi
  done
}

check_version() {
  check_existing
  if [[ -x "$INSTALL_DIR/esprit" ]]; then
    installed_version=$("$INSTALL_DIR/esprit" --version 2>/dev/null | awk '{print $2}' || echo "")
    if [[ "$installed_version" == "$specific_version" ]]; then
      echo -e "${GREEN}✓ Esprit $specific_version already installed${NC}"
      SKIP_DOWNLOAD=true
    elif [[ -n "$installed_version" ]]; then
      echo -e "${DIM}Installed: ${NC}$installed_version ${DIM}→ ${NC}$specific_version"
    fi
  fi
}

download_and_install() {
  echo ""
  echo -e "${CYAN}${BOLD}   ███████╗███████╗██████╗ ██████╗ ██╗████████╗${NC}"
  echo -e "${CYAN}${BOLD}   ██╔════╝██╔════╝██╔══██╗██╔══██╗██║╚══██╔══╝${NC}"
  echo -e "${CYAN}${BOLD}   █████╗  ███████╗██████╔╝██████╔╝██║   ██║   ${NC}"
  echo -e "${CYAN}${BOLD}   ██╔══╝  ╚════██║██╔═══╝ ██╔══██╗██║   ██║   ${NC}"
  echo -e "${CYAN}${BOLD}   ███████╗███████║██║     ██║  ██║██║   ██║   ${NC}"
  echo -e "${CYAN}${BOLD}   ╚══════╝╚══════╝╚═╝     ╚═╝  ╚═╝╚═╝   ╚═╝   ${NC}"
  echo ""
  echo -e "${DIM}  AI-Powered Penetration Testing${NC}"
  echo ""
  echo -e "  ${DIM}Version:${NC}  $specific_version"
  echo -e "  ${DIM}Platform:${NC} $target"
  echo ""

  local tmp_dir=$(mktemp -d)
  cd "$tmp_dir"

  echo -e "${DIM}Downloading...${NC}"
  curl -# -L -o "$filename" "$url"

  if [ ! -f "$filename" ]; then
    echo -e "${RED}Download failed${NC}"
    exit 1
  fi

  echo -e "${DIM}Extracting...${NC}"
  tar -xzf "$filename"

  if [ -f "esprit" ]; then
    mv "esprit" "$INSTALL_DIR/esprit"
  elif ls esprit-* 1>/dev/null 2>&1; then
    mv esprit-* "$INSTALL_DIR/esprit"
  else
    echo -e "${RED}Binary not found in archive${NC}"
    exit 1
  fi

  chmod 755 "$INSTALL_DIR/esprit"
  cd - > /dev/null
  rm -rf "$tmp_dir"

  echo -e "${GREEN}✓ Installed to $INSTALL_DIR/esprit${NC}"
}

add_to_path() {
  local config_file=$1 command=$2
  if grep -Fxq "$command" "$config_file" 2>/dev/null; then
    echo -e "${DIM}PATH already configured in ${NC}$config_file"
  elif [[ -w $config_file ]]; then
    echo -e "\n# esprit" >> "$config_file"
    echo "$command" >> "$config_file"
    echo -e "${DIM}Added esprit to \$PATH in ${NC}$config_file"
  fi
}

setup_path() {
  current_shell=$(basename "$SHELL")
  case $current_shell in
    fish)  config_files="$HOME/.config/fish/config.fish" ;;
    zsh)   config_files="${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash)  config_files="$HOME/.bashrc $HOME/.bash_profile" ;;
    *)     config_files="$HOME/.bashrc $HOME/.bash_profile" ;;
  esac

  config_file=""
  for file in $config_files; do
    [[ -f $file ]] && config_file=$file && break
  done

  if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    if [[ -z $config_file ]]; then
      echo -e "${YELLOW}Add to your PATH:${NC}"
      echo -e "  export PATH=$INSTALL_DIR:\$PATH"
    else
      case $current_shell in
        fish) add_to_path "$config_file" "fish_add_path $INSTALL_DIR" ;;
        *)    add_to_path "$config_file" "export PATH=$INSTALL_DIR:\$PATH" ;;
      esac
    fi
  fi

  [ -n "${GITHUB_ACTIONS-}" ] && [ "${GITHUB_ACTIONS}" == "true" ] && echo "$INSTALL_DIR" >> "$GITHUB_PATH"
}

check_docker() {
  echo ""
  if ! command -v docker >/dev/null 2>&1; then
    echo -e "${YELLOW}  Docker not found${NC}"
    echo -e "${DIM}  Esprit requires Docker for security scans.${NC}"
    echo -e "${DIM}  Install: ${NC}https://docs.docker.com/get-docker/"
    return 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo -e "${YELLOW}  Docker daemon not running${NC}"
    return 1
  fi
  echo -e "${GREEN}  ✓ Docker available${NC}"
  return 0
}

verify_installation() {
  export PATH="$INSTALL_DIR:$PATH"
  if [[ -x "$INSTALL_DIR/esprit" ]]; then
    local version=$("$INSTALL_DIR/esprit" --version 2>/dev/null | awk '{print $2}' || echo "$specific_version")
    echo -e "${GREEN}  ✓ Esprit ${NC}$version${GREEN} ready${NC}"
  fi
}

# ─── Run ─────────────────────────────────────────────────────
check_version
[ "$SKIP_DOWNLOAD" = false ] && download_and_install
setup_path
echo ""
verify_installation
check_docker || true

# Check for OpenCode
[ -f "$HOME/.local/share/opencode/auth.json" ] && echo "" && echo -e "${CYAN}  OpenCode credentials detected${NC}" && echo -e "${DIM}  Run 'esprit provider import-opencode' to import them.${NC}"

echo ""
echo -e "${BOLD}  Getting Started${NC}"
echo ""
echo -e "  ${CYAN}1.${NC} Login to your LLM provider:"
echo -e "     ${DIM}esprit provider login${NC}"
echo ""
echo -e "  ${CYAN}2.${NC} Or set model + API key:"
echo -e "     ${DIM}export ESPRIT_LLM='openai/gpt-5'${NC}"
echo -e "     ${DIM}export LLM_API_KEY='your-api-key'${NC}"
echo ""
echo -e "  ${CYAN}3.${NC} Run a scan:"
echo -e "     ${DIM}esprit scan https://example.com${NC}"
echo ""
echo -e "${DIM}  https://esprit.dev${NC}"
echo ""
echo -e "${YELLOW}  →${NC} Run ${DIM}source ~/.$(basename $SHELL)rc${NC} or open a new terminal"
echo ""
