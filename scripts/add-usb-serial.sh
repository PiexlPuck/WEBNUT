#!/usr/bin/env bash
set -euo pipefail

# WEBNUT USB & USB-Serial Passthrough Wizard
# Can be invoked directly (`./scripts/add-usb-serial.sh`) whenever adding a new USB/Serial UPS,
# or called from `scripts/install.sh --from-installer`.

FROM_INSTALLER=0
COMPOSE_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --from-installer)
      FROM_INSTALLER=1
      shift
      ;;
    --compose-dir)
      COMPOSE_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat << 'EOF'
Usage: add-usb-serial.sh [--from-installer] [--compose-dir <path>]

Interactive wizard to detect a newly plugged-in USB or USB-Serial UPS adapter,
optionally create a persistent udev symlink (/dev/webnut-ups-<alias>), and add
the device passthrough mapping to docker-compose.override.yml.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
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

resolve_compose_dir() {
  if [ -n "$COMPOSE_DIR" ] && [ -f "$COMPOSE_DIR/docker-compose.yml" ]; then
    return 0
  fi
  local script_dir=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [ -f "$script_dir/docker-compose.yml" ]; then
      COMPOSE_DIR="$script_dir"
      return 0
    fi
  fi
  if [ -f "$PWD/docker-compose.yml" ]; then
    COMPOSE_DIR="$PWD"
    return 0
  fi
  if [ -f "$PWD/WEBNUT/docker-compose.yml" ]; then
    COMPOSE_DIR="$PWD/WEBNUT"
    return 0
  fi
  local input_dir=""
  read_tty "Enter path to your WEBNUT directory (containing docker-compose.yml): " input_dir
  if [ -n "$input_dir" ] && [ -f "$input_dir/docker-compose.yml" ]; then
    COMPOSE_DIR="$(cd "$input_dir" && pwd)"
    return 0
  fi
  echo "Error: Could not locate docker-compose.yml." >&2
  exit 1
}

snapshot_tty_devices() {
  shopt -s nullglob
  local d
  for d in /dev/ttyUSB* /dev/ttyACM*; do
    [ -e "$d" ] && echo "$d"
  done
  shopt -u nullglob
}

snapshot_lsusb() {
  if command -v lsusb >/dev/null 2>&1; then
    lsusb 2>/dev/null | sort || true
  fi
}

find_by_id_for_tty() {
  local target_real
  target_real="$(readlink -f "$1" 2>/dev/null || echo "$1")"
  shopt -s nullglob
  local link
  for link in /dev/serial/by-id/*; do
    if [ -e "$link" ]; then
      local resolved
      resolved="$(readlink -f "$link" 2>/dev/null || true)"
      if [ "$resolved" = "$target_real" ]; then
        echo "$link"
        shopt -u nullglob
        return 0
      fi
    fi
  done
  shopt -u nullglob
  return 1
}

get_usb_attr_for_tty() {
  local dev_node="$1"
  local tty_name
  tty_name="$(basename "$dev_node")"
  local sys_path="/sys/class/tty/${tty_name}/device"
  if [ ! -e "$sys_path" ]; then
    return 0
  fi
  local cur
  cur="$(readlink -f "$sys_path" 2>/dev/null || true)"
  while [ -n "$cur" ] && [ "$cur" != "/" ]; do
    if [ -f "$cur/idVendor" ] && [ -f "$cur/idProduct" ]; then
      local vid pid serial mfr prod
      vid="$(tr -d '\n\r' < "$cur/idVendor" 2>/dev/null || true)"
      pid="$(tr -d '\n\r' < "$cur/idProduct" 2>/dev/null || true)"
      serial="$(tr -d '\n\r' < "$cur/serial" 2>/dev/null || true)"
      mfr="$(tr -d '\n\r' < "$cur/manufacturer" 2>/dev/null || true)"
      prod="$(tr -d '\n\r' < "$cur/product" 2>/dev/null || true)"
      printf "%s|%s|%s|%s|%s\n" "$vid" "$pid" "$serial" "$mfr" "$prod"
      return 0
    fi
    cur="$(dirname "$cur")"
  done
}

add_device_to_override() {
  local host_dev="$1"
  local container_dev="$2"
  local override_file="$COMPOSE_DIR/docker-compose.override.yml"
  local mapping="${host_dev}:${container_dev}"

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$override_file" "$mapping" << 'PYEOF'
import pathlib, sys

path = pathlib.Path(sys.argv[1])
mapping = sys.argv[2]
devices = []

if path.exists():
    in_devices = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "devices:":
            in_devices = True
            continue
        if in_devices:
            if stripped.startswith("- "):
                val = stripped[2:].strip().strip('"').strip("'")
                if val and val not in devices:
                    devices.append(val)
            elif stripped and not stripped.startswith("#"):
                in_devices = False

if mapping not in devices:
    devices.append(mapping)

lines = [
    "# Managed by WEBNUT scripts/add-usb-serial.sh",
    "services:",
    "  webnut:",
    "    devices:",
]
for d in devices:
    lines.append(f'      - "{d}"')
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYEOF
  else
    if [ -f "$override_file" ] && grep -Fq "$mapping" "$override_file"; then
      return 0
    fi
    if [ ! -f "$override_file" ]; then
      cat > "$override_file" << EOF
# Managed by WEBNUT scripts/add-usb-serial.sh
services:
  webnut:
    devices:
      - "${mapping}"
EOF
    else
      printf '      - "%s"\n' "$mapping" >> "$override_file"
    fi
  fi
}

create_udev_rule_if_requested() {
  local vid="$1"
  local pid="$2"
  local serial="$3"
  local alias_name="$4"
  local rule_file="/etc/udev/rules.d/99-webnut-ups.rules"
  local symlink_name="webnut-ups-${alias_name}"

  if [ -z "$vid" ] || [ -z "$pid" ]; then
    echo ""
    return 1
  fi

  local rule_line
  if [ -n "$serial" ]; then
    rule_line="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"${vid}\", ATTRS{idProduct}==\"${pid}\", ATTRS{serial}==\"${serial}\", SYMLINK+=\"${symlink_name}\", MODE=\"0660\", GROUP=\"dialout\""
  else
    rule_line="SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"${vid}\", ATTRS{idProduct}==\"${pid}\", SYMLINK+=\"${symlink_name}\", MODE=\"0660\", GROUP=\"dialout\""
  fi

  local runner=""
  if [ "$(id -u)" -eq 0 ]; then
    runner="direct"
  elif command -v sudo >/dev/null 2>&1; then
    runner="sudo"
  else
    return 1
  fi

  local tmp_rule
  tmp_rule="$(mktemp)"
  if [ -f "$rule_file" ]; then
    grep -v "SYMLINK+=\"${symlink_name}\"" "$rule_file" > "$tmp_rule" || true
  fi
  echo "$rule_line" >> "$tmp_rule"

  if [ "$runner" = "direct" ]; then
    cp "$tmp_rule" "$rule_file"
    chmod 644 "$rule_file"
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=tty 2>/dev/null || true
  else
    sudo cp "$tmp_rule" "$rule_file"
    sudo chmod 644 "$rule_file"
    sudo udevadm control --reload-rules 2>/dev/null || true
    sudo udevadm trigger --subsystem-match=tty 2>/dev/null || true
  fi
  rm -f "$tmp_rule"

  if command -v udevadm >/dev/null 2>&1; then
    udevadm settle --timeout=5 2>/dev/null || true
  fi
  sleep 1
  if [ -e "/dev/${symlink_name}" ]; then
    echo "/dev/${symlink_name}"
    return 0
  fi
  echo "/dev/${symlink_name}"
  return 0
}

configure_serial_node() {
  local dev_node="$1"
  local by_id
  by_id="$(find_by_id_for_tty "$dev_node" || true)"
  local meta
  meta="$(get_usb_attr_for_tty "$dev_node")"
  local vid="" pid="" serial="" mfr="" prod=""
  if [ -n "$meta" ]; then
    IFS="|" read -r vid pid serial mfr prod <<< "$meta"
  fi

  echo ""
  echo "  Detected Serial Device: $dev_node"
  [ -n "$by_id" ] && echo "  Persistent by-id:       $by_id"
  [ -n "$vid" ] && echo "  USB ID:                 ${vid}:${pid} (${mfr:-Unknown} ${prod:-})"
  [ -n "$serial" ] && echo "  Serial Number:          $serial"

  local default_alias
  default_alias="$(basename "$dev_node" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
  local host_dev="${by_id:-$dev_node}"
  local container_dev="$host_dev"

  if [ -n "$vid" ] && [ -n "$pid" ]; then
    local create_udev=""
    read_tty "  Create a persistent udev symlink (/dev/webnut-ups-<alias>)? [Y/n]: " create_udev
    if [ "${create_udev:-Y}" != "n" ] && [ "${create_udev:-Y}" != "N" ]; then
      local alias_in=""
      read_tty "  Friendly alias for symlink [${default_alias}]: " alias_in
      local clean_alias
      clean_alias="$(printf "%s" "${alias_in:-$default_alias}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
      clean_alias="${clean_alias:-$default_alias}"
      local udev_sym=""
      if udev_sym="$(create_udev_rule_if_requested "$vid" "$pid" "$serial" "$clean_alias")"; then
        host_dev="$udev_sym"
        container_dev="$udev_sym"
        echo "  -> Installed udev symlink: $udev_sym"
      else
        echo "  -> Could not write /etc/udev/rules.d (no root/sudo); using $host_dev"
      fi
    fi
  fi

  add_device_to_override "$host_dev" "$container_dev"
  echo "  -> Added passthrough to docker-compose.override.yml: ${host_dev}:${container_dev}"
  ADDED_COUNT=$((ADDED_COUNT + 1))
}

resolve_compose_dir
ADDED_COUNT=0

echo ""
echo "=========================================================="
echo "  WEBNUT USB & USB-Serial UPS Passthrough Wizard"
echo "=========================================================="
echo "Compose directory: $COMPOSE_DIR"
echo ""

while true; do
  local_mode=""
  echo "To accurately detect your USB or USB-Serial adapter:"
  read_tty "Make sure the UPS USB/Serial cable is UNPLUGGED, then press [Enter] (or [S] to list already-connected devices): " local_mode

  if [ "${local_mode:-}" = "s" ] || [ "${local_mode:-}" = "S" ]; then
    mapfile -t existing_ttys < <(snapshot_tty_devices)
    if [ "${#existing_ttys[@]}" -eq 0 ]; then
      echo "No /dev/ttyUSB* or /dev/ttyACM* devices currently found."
      echo "If you have a standard USB HID UPS (non-serial), /dev/bus/usb is already passed through in docker-compose.yml."
    else
      echo ""
      echo "Currently connected serial devices:"
      local idx=1
      for t in "${existing_ttys[@]}"; do
        local bid
        bid="$(find_by_id_for_tty "$t" || true)"
        echo "  [$idx] $t ${bid:+(by-id: $bid)}"
        idx=$((idx + 1))
      done
      local pick=""
      read_tty "Select device number to add (or [0] to cancel): " pick
      if [[ "${pick:-0}" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#existing_ttys[@]}" ]; then
        configure_serial_node "${existing_ttys[$((pick - 1))]}"
      fi
    fi
  else
    before_ttys="$(snapshot_tty_devices)"
    before_usb="$(snapshot_lsusb)"

    echo ""
    read_tty ">>> Please PLUG IN your USB / USB-Serial UPS adapter now, wait 2 seconds, then press [Enter] to scan... " _pause

    if command -v udevadm >/dev/null 2>&1; then
      udevadm settle --timeout=5 2>/dev/null || true
    fi
    sleep 2

    after_ttys="$(snapshot_tty_devices)"
    after_usb="$(snapshot_lsusb)"

    mapfile -t new_ttys < <(comm -13 <(printf "%s\n" "$before_ttys" | sort) <(printf "%s\n" "$after_ttys" | sort) | sed '/^$/d')
    mapfile -t new_usb < <(comm -13 <(printf "%s\n" "$before_usb") <(printf "%s\n" "$after_usb") | sed '/^$/d')

    if [ "${#new_ttys[@]}" -gt 0 ]; then
      for dev_node in "${new_ttys[@]}"; do
        configure_serial_node "$dev_node"
      done
    elif [ "${#new_usb[@]}" -gt 0 ]; then
      echo ""
      echo "Detected new USB device(s) (no ttyUSB/ttyACM serial node created):"
      for line in "${new_usb[@]}"; do
        echo "  * $line"
      done
      echo "Standard USB HID UPS devices are already accessible via /dev/bus/usb:/dev/bus/usb in docker-compose.yml."
      echo "Once WEBNUT starts, use 'NUT Config -> Scan Hardware (USB & Serial)' in the Web UI to add it."
    else
      echo ""
      echo "No new USB or Serial device detected."
      local retry_choice=""
      read_tty "Choose [R]etry plug-in scan, [L]ist existing serial ports, or [C]ancel [R/l/c]: " retry_choice
      case "${retry_choice:-R}" in
        l|L)
          mapfile -t existing_ttys < <(snapshot_tty_devices)
          if [ "${#existing_ttys[@]}" -eq 0 ]; then
            echo "No /dev/ttyUSB* or /dev/ttyACM* ports present."
          else
            local idx=1
            for t in "${existing_ttys[@]}"; do
              echo "  [$idx] $t"
              idx=$((idx + 1))
            done
            local pick=""
            read_tty "Select device number to add (or [0] to skip): " pick
            if [[ "${pick:-0}" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#existing_ttys[@]}" ]; then
              configure_serial_node "${existing_ttys[$((pick - 1))]}"
            fi
          fi
          ;;
        c|C)
          ;;
        *)
          continue
          ;;
      esac
    fi
  fi

  local another=""
  read_tty "Configure another USB / USB-Serial UPS adapter? [y/N]: " another
  if [ "${another:-N}" != "y" ] && [ "${another:-N}" != "Y" ]; then
    break
  fi
done

if [ "$FROM_INSTALLER" -eq 0 ] && [ "$ADDED_COUNT" -gt 0 ]; then
  echo ""
  local apply_now=""
  read_tty "Apply changes and recreate the WEBNUT container now ('docker compose up -d')? [Y/n]: " apply_now
  if [ "${apply_now:-Y}" != "n" ] && [ "${apply_now:-Y}" != "N" ]; then
    (cd "$COMPOSE_DIR" && docker compose up -d)
    echo ""
    echo "Container updated! Open WEBNUT -> NUT Config -> Scan Hardware (USB & Serial) to see your new device."
  else
    echo "Run 'cd \"$COMPOSE_DIR\" && docker compose up -d' when ready."
  fi
fi
