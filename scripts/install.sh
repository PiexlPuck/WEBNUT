#!/usr/bin/env bash
set -euo pipefail

# WEBNUT One-Line Interactive Installer & Updater
# Install or update interactively:
#   curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash
# Non-interactive in-place update:
#   curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash -s -- --update

REPO_URL="${WEBNUT_REPO_URL:-https://github.com/PiexlPuck/WEBNUT.git}"
DEFAULT_DIR="${WEBNUT_INSTALL_DIR:-$PWD/WEBNUT}"

AUTO_UPDATE=0
FORCE_RECONFIGURE=0
TARGET_DIR_ARG=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --update)
      AUTO_UPDATE=1
      shift
      ;;
    --reconfigure)
      FORCE_RECONFIGURE=1
      shift
      ;;
    --dir)
      TARGET_DIR_ARG="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat << 'EOF'
Usage: install.sh [--update] [--reconfigure] [--dir <path>]

Install WEBNUT cleanly on a fresh host, or update an existing WEBNUT
installation in-place while preserving .env, data/, and USB passthrough.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

read_tty() {
  local prompt="$1"
  local varname="$2"
  local val=""
  if [ -r /dev/tty ]; then
    printf "%s" "$prompt" > /dev/tty
    IFS= read -r val < /dev/tty || true
  else
    printf "%s" "$prompt"
    IFS= read -r val || true
  fi
  printf -v "$varname" "%s" "$val"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: '$1' is required but not installed." >&2
    exit 1
  fi
}

detect_timezone() {
  if command -v timedatectl >/dev/null 2>&1; then
    local tz
    tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    if [ -n "$tz" ]; then
      echo "$tz"
      return
    fi
  fi
  if [ -f /etc/timezone ]; then
    local tz
    tz="$(tr -d ' \t\r\n' < /etc/timezone 2>/dev/null || true)"
    if [ -n "$tz" ]; then
      echo "$tz"
      return
    fi
  fi
  echo "UTC"
}

is_port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]" && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]" && return 0
  fi
  return 1
}

run_env_wizard() {
  local default_tz in_tz in_web in_nut use_anyway
  default_tz="$(detect_timezone)"
  read_tty "Timezone (TZ) [${default_tz}]: " in_tz
  TZ_VAL="${in_tz:-$default_tz}"

  while true; do
    read_tty "Web UI Port (WEB_PORT) [8765]: " in_web
    WEB_PORT_VAL="${in_web:-8765}"
    if ! [[ "$WEB_PORT_VAL" =~ ^[0-9]+$ ]] || [ "$WEB_PORT_VAL" -lt 1 ] || [ "$WEB_PORT_VAL" -gt 65535 ]; then
      echo "  Invalid port number. Enter 1-65535."
      continue
    fi
    if is_port_in_use "$WEB_PORT_VAL"; then
      echo "  Warning: Port $WEB_PORT_VAL appears to be in use on this host."
      read_tty "  Use $WEB_PORT_VAL anyway? [y/N]: " use_anyway
      [ "${use_anyway:-N}" = "y" ] || [ "${use_anyway:-N}" = "Y" ] || continue
    fi
    break
  done

  while true; do
    read_tty "NUT Server Port (NUT_PORT) [3493]: " in_nut
    NUT_PORT_VAL="${in_nut:-3493}"
    if ! [[ "$NUT_PORT_VAL" =~ ^[0-9]+$ ]] || [ "$NUT_PORT_VAL" -lt 1 ] || [ "$NUT_PORT_VAL" -gt 65535 ]; then
      echo "  Invalid port number. Enter 1-65535."
      continue
    fi
    if is_port_in_use "$NUT_PORT_VAL"; then
      echo "  Warning: Port $NUT_PORT_VAL is already in use (if host nut-server is running, stop it or choose another port)."
      read_tty "  Use $NUT_PORT_VAL anyway? [y/N]: " use_anyway
      [ "${use_anyway:-N}" = "y" ] || [ "${use_anyway:-N}" = "Y" ] || continue
    fi
    break
  done

  cat > .env << EOF
TZ=${TZ_VAL}
WEB_PORT=${WEB_PORT_VAL}
NUT_PORT=${NUT_PORT_VAL}
EOF
  echo "Created $INSTALL_DIR/.env"
}

pull_repo_updates() {
  echo "Fetching updates from origin/main..."
  git fetch origin main --quiet || git fetch origin --quiet
  if git pull --ff-only origin main; then
    return 0
  fi
  echo ""
  echo "Fast-forward pull encountered local modifications to tracked files."
  echo "Note: .env, docker-compose.override.yml, and ./data/ are untracked and will be preserved."
  local reset_choice="N"
  if [ "$AUTO_UPDATE" -eq 1 ]; then
    reset_choice="Y"
  else
    read_tty "Reset tracked files to origin/main? [y/N]: " reset_choice
  fi
  if [ "${reset_choice:-N}" = "y" ] || [ "${reset_choice:-N}" = "Y" ]; then
    git reset --hard origin/main
  else
    echo "Update aborted; resolve local git state manually." >&2
    exit 1
  fi
}

echo "=========================================================="
echo "  WEBNUT — Unified NUT & WOLNUT Web Manager Installer"
echo "=========================================================="
echo ""

require_cmd git
require_cmd docker
if ! docker compose version >/dev/null 2>&1; then
  echo "Error: Docker Compose v2 plugin ('docker compose') is required." >&2
  exit 1
fi

# Locate existing installation or choose target directory
INSTALL_DIR=""
IS_EXISTING=0

if [ -n "$TARGET_DIR_ARG" ]; then
  INSTALL_DIR="$TARGET_DIR_ARG"
elif [ -f "$PWD/docker-compose.yml" ] && [ -f "$PWD/backend/app/main.py" ]; then
  INSTALL_DIR="$PWD"
elif [ "$AUTO_UPDATE" -eq 1 ] && [ -d "$DEFAULT_DIR/.git" ]; then
  INSTALL_DIR="$DEFAULT_DIR"
else
  local_dir=""
  read_tty "Install / existing WEBNUT directory [${DEFAULT_DIR}]: " local_dir
  INSTALL_DIR="${local_dir:-$DEFAULT_DIR}"
fi

if [ -d "$INSTALL_DIR/.git" ] && [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
  IS_EXISTING=1
elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
  echo "Error: Directory '$INSTALL_DIR' already exists and is not a WEBNUT git repository." >&2
  exit 1
fi

if [ "$IS_EXISTING" -eq 1 ]; then
  cd "$INSTALL_DIR"
  LOCAL_VER="unknown"
  [ -f VERSION ] && LOCAL_VER="$(tr -d ' \t\r\n' < VERSION)"
  git fetch origin main --quiet 2>/dev/null || true
  REMOTE_VER="$(git show origin/main:VERSION 2>/dev/null | tr -d ' \t\r\n' || echo "unknown")"

  echo "Found existing WEBNUT installation at: $INSTALL_DIR"
  echo "  Installed version: v${LOCAL_VER}"
  echo "  Latest on GitHub:  v${REMOTE_VER}"
  echo ""

  ACTION="U"
  if [ "$AUTO_UPDATE" -eq 1 ]; then
    ACTION="U"
  elif [ "$FORCE_RECONFIGURE" -eq 1 ]; then
    ACTION="R"
  else
    echo "Choose an action:"
    echo "  [U] Update WEBNUT in-place (preserves .env, data/, and USB passthrough)"
    echo "  [R] Update & Reconfigure (.env + USB/Serial wizard)"
    echo "  [A] Add/Configure USB or USB-Serial adapter only"
    read_tty "Select [U/r/a]: " ACTION
    ACTION="${ACTION:-U}"
  fi

  case "$ACTION" in
    a|A)
      chmod +x scripts/add-usb-serial.sh 2>/dev/null || true
      bash "$INSTALL_DIR/scripts/add-usb-serial.sh" --compose-dir "$INSTALL_DIR"
      exit 0
      ;;
    r|R)
      pull_repo_updates
      chmod +x scripts/install.sh scripts/add-usb-serial.sh docker/entrypoint.sh 2>/dev/null || true
      echo ""
      echo "--- Step 1: Environment Configuration (.env) ---"
      run_env_wizard
      echo ""
      echo "--- Step 2: USB / USB-Serial UPS Hardware Setup ---"
      read_tty "Configure a USB / USB-Serial UPS adapter now? [Y/n]: " setup_usb
      if [ "${setup_usb:-Y}" != "n" ] && [ "${setup_usb:-Y}" != "N" ]; then
        bash "$INSTALL_DIR/scripts/add-usb-serial.sh" --from-installer --compose-dir "$INSTALL_DIR"
      fi
      ;;
    *)
      pull_repo_updates
      chmod +x scripts/install.sh scripts/add-usb-serial.sh docker/entrypoint.sh 2>/dev/null || true
      if [ ! -f ".env" ]; then
        cp -n .env.example .env 2>/dev/null || true
      fi
      ;;
  esac
else
  echo "Cloning WEBNUT into $INSTALL_DIR..."
  git clone "$REPO_URL" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
  chmod +x scripts/install.sh scripts/add-usb-serial.sh docker/entrypoint.sh 2>/dev/null || true

  echo ""
  echo "--- Step 1: Environment Configuration (.env) ---"
  run_env_wizard

  echo ""
  echo "--- Step 2: USB / USB-Serial UPS Hardware Setup ---"
  setup_usb=""
  read_tty "Would you like to plug in and configure a USB / USB-Serial UPS now? [Y/n]: " setup_usb
  if [ "${setup_usb:-Y}" != "n" ] && [ "${setup_usb:-Y}" != "N" ]; then
    bash "$INSTALL_DIR/scripts/add-usb-serial.sh" --from-installer --compose-dir "$INSTALL_DIR"
  else
    echo "Skipping USB-Serial wizard. You can add USB/Serial adapters later anytime with:"
    echo "  cd \"$INSTALL_DIR\" && ./scripts/add-usb-serial.sh"
  fi
fi

# Read WEB_PORT for health check
WEB_PORT_VAL="8765"
if [ -f ".env" ]; then
  # shellcheck disable=SC1091
  set -a && source .env && set +a
  WEB_PORT_VAL="${WEB_PORT:-8765}"
fi

echo ""
echo "--- Building & Starting WEBNUT ---"
docker compose up -d --build

echo "Waiting for WEBNUT API on port ${WEB_PORT_VAL}..."
HEALTH_OK=0
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT_VAL}/api/health" >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done

NEW_VER="unknown"
[ -f VERSION ] && NEW_VER="$(tr -d ' \t\r\n' < VERSION)"
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")"

echo ""
echo "=========================================================="
if [ "$HEALTH_OK" -eq 1 ]; then
  echo "  WEBNUT v${NEW_VER} is UP and healthy!"
else
  echo "  WEBNUT v${NEW_VER} container started (still initializing)."
fi
echo "  Web UI: http://${HOST_IP}:${WEB_PORT_VAL}"
echo "  Add USB/Serial adapters later: cd \"$INSTALL_DIR\" && ./scripts/add-usb-serial.sh"
echo "  One-line update anytime:       curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash -s -- --update"
echo "=========================================================="
