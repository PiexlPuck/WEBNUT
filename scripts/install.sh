#!/usr/bin/env bash
set -euo pipefail

# WEBNUT One-Line Interactive Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash

REPO_URL="${WEBNUT_REPO_URL:-https://github.com/PiexlPuck/WEBNUT.git}"
DEFAULT_DIR="${WEBNUT_INSTALL_DIR:-$PWD/WEBNUT}"

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

# Determine or clone repository directory
INSTALL_DIR=""
if [ -f "$PWD/docker-compose.yml" ] && [ -f "$PWD/backend/app/main.py" ]; then
  INSTALL_DIR="$PWD"
  echo "Detected existing WEBNUT repository at: $INSTALL_DIR"
else
  local_dir=""
  read_tty "Install directory [${DEFAULT_DIR}]: " local_dir
  INSTALL_DIR="${local_dir:-$DEFAULT_DIR}"
  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Existing git repository found at $INSTALL_DIR."
    do_pull=""
    read_tty "Pull latest updates from origin/main? [Y/n]: " do_pull
    if [ "${do_pull:-Y}" != "n" ] && [ "${do_pull:-Y}" != "N" ]; then
      git -C "$INSTALL_DIR" pull --ff-only
    fi
  elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
    echo "Error: Directory '$INSTALL_DIR' already exists and is not a WEBNUT git repository." >&2
    exit 1
  else
    echo "Cloning WEBNUT into $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
fi

cd "$INSTALL_DIR"
chmod +x scripts/install.sh scripts/add-usb-serial.sh docker/entrypoint.sh 2>/dev/null || true

# Interactive .env setup
echo ""
echo "--- Step 1: Environment Configuration (.env) ---"
write_env="Y"
if [ -f ".env" ]; then
  echo "An existing .env file was found:"
  sed 's/^/  /' .env
  read_tty "Overwrite existing .env? [y/N]: " write_env
  write_env="${write_env:-N}"
fi

if [ "$write_env" = "y" ] || [ "$write_env" = "Y" ]; then
  default_tz="$(detect_timezone)"
  in_tz=""
  read_tty "Timezone (TZ) [${default_tz}]: " in_tz
  TZ_VAL="${in_tz:-$default_tz}"

  while true; do
    in_web=""
    read_tty "Web UI Port (WEB_PORT) [8765]: " in_web
    WEB_PORT_VAL="${in_web:-8765}"
    if ! [[ "$WEB_PORT_VAL" =~ ^[0-9]+$ ]] || [ "$WEB_PORT_VAL" -lt 1 ] || [ "$WEB_PORT_VAL" -gt 65535 ]; then
      echo "  Invalid port number. Enter 1-65535."
      continue
    fi
    if is_port_in_use "$WEB_PORT_VAL"; then
      echo "  Warning: Port $WEB_PORT_VAL appears to be in use on this host."
      use_anyway=""
      read_tty "  Use $WEB_PORT_VAL anyway? [y/N]: " use_anyway
      [ "${use_anyway:-N}" = "y" ] || [ "${use_anyway:-N}" = "Y" ] || continue
    fi
    break
  done

  while true; do
    in_nut=""
    read_tty "NUT Server Port (NUT_PORT) [3493]: " in_nut
    NUT_PORT_VAL="${in_nut:-3493}"
    if ! [[ "$NUT_PORT_VAL" =~ ^[0-9]+$ ]] || [ "$NUT_PORT_VAL" -lt 1 ] || [ "$NUT_PORT_VAL" -gt 65535 ]; then
      echo "  Invalid port number. Enter 1-65535."
      continue
    fi
    if is_port_in_use "$NUT_PORT_VAL"; then
      echo "  Warning: Port $NUT_PORT_VAL is already in use (if host nut-server is running, stop it or choose another port)."
      use_anyway=""
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
else
  # shellcheck disable=SC1091
  set -a && source .env && set +a
  WEB_PORT_VAL="${WEB_PORT:-8765}"
  NUT_PORT_VAL="${NUT_PORT:-3493}"
fi

# Interactive USB / USB-Serial Plug-in & Passthrough Wizard
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

# Build and start container
echo ""
echo "--- Step 3: Building & Starting WEBNUT ---"
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

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")"
echo ""
echo "=========================================================="
if [ "$HEALTH_OK" -eq 1 ]; then
  echo "  WEBNUT is UP and healthy!"
else
  echo "  WEBNUT container started (still initializing)."
fi
echo "  Web UI: http://${HOST_IP}:${WEB_PORT_VAL}"
echo "  Add USB/Serial adapters later: cd \"$INSTALL_DIR\" && ./scripts/add-usb-serial.sh"
echo "=========================================================="
