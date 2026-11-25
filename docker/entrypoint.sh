#!/usr/bin/env bash
set -euo pipefail

NUT_DIR="${WEBNUT_NUT_DIR:-/etc/nut}"
DATA_DIR="${WEBNUT_DATA_DIR:-/app/data}"
NUT_PORT="${NUT_PORT:-3493}"

mkdir -p "${DATA_DIR}" "${NUT_DIR}" "${NUT_DIR}/.backups" /run/nut /var/run/nut

# Seed default NUT configuration files on first run if missing
if [ ! -f "${NUT_DIR}/nut.conf" ]; then
  cat > "${NUT_DIR}/nut.conf" << 'EOF'
# Managed by WEBNUT
MODE=netserver
EOF
fi

if [ ! -f "${NUT_DIR}/ups.conf" ]; then
  cat > "${NUT_DIR}/ups.conf" << 'EOF'
# Managed by WEBNUT
# Add UPS definitions via the Web UI ("Scan USB UPS" or manual entry)
maxretry = 3
EOF
fi

if [ ! -f "${NUT_DIR}/upsd.conf" ]; then
  cat > "${NUT_DIR}/upsd.conf" << EOF
# Managed by WEBNUT
LISTEN 0.0.0.0 ${NUT_PORT}
EOF
fi

if [ ! -f "${NUT_DIR}/upsd.users" ]; then
  ADMIN_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
  MON_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
  cat > "${NUT_DIR}/upsd.users" << EOF
# Managed by WEBNUT
[admin]
  password = ${ADMIN_PW}
  actions = SET
  instcmds = ALL

[monuser]
  password = ${MON_PW}
  upsmon primary
EOF
  unset ADMIN_PW MON_PW
fi

if [ ! -f "${NUT_DIR}/upsmon.conf" ]; then
  cat > "${NUT_DIR}/upsmon.conf" << 'EOF'
# Managed by WEBNUT
RUN_AS_USER nut
MINSUPPLIES 1
SHUTDOWNCMD "/bin/true"
POLLFREQ 5
POLLFREQALERT 5
HOSTSYNC 15
DEADTIME 15
POWERDOWNFLAG /etc/killpower
NOTIFYFLAG ONLINE SYSLOG+WALL
NOTIFYFLAG ONBATT SYSLOG+WALL
NOTIFYFLAG LOWBATT SYSLOG+WALL
NOTIFYFLAG FSD SYSLOG+WALL
NOTIFYFLAG COMMOK SYSLOG+WALL
NOTIFYFLAG COMMBAD SYSLOG+WALL
NOTIFYFLAG SHUTDOWN SYSLOG+WALL
NOTIFYFLAG REPLBATT SYSLOG+WALL
NOTIFYFLAG NOCOMM SYSLOG+WALL
NOTIFYFLAG NOPARENT SYSLOG+WALL
FINALDELAY 5
EOF
fi

# Enforce strict ownership and permissions required by upsd/upsmon
if id -u nut >/dev/null 2>&1; then
  usermod -aG dialout nut 2>/dev/null || true
  chown -R nut:nut "${NUT_DIR}" /run/nut /var/run/nut
fi

chmod 750 "${NUT_DIR}" "${NUT_DIR}/.backups" /run/nut /var/run/nut

shopt -s nullglob
for cfg in "${NUT_DIR}"/*.conf "${NUT_DIR}"/upsd.users; do
  chmod 640 "${cfg}"
done
for tty_dev in /dev/webnut-ups-* /dev/ttyUSB* /dev/ttyACM* /dev/serial/by-id/*; do
  if [ -e "${tty_dev}" ]; then
    real_tty="$(readlink -f "${tty_dev}" 2>/dev/null || echo "${tty_dev}")"
    if id -u nut >/dev/null 2>&1; then
      chgrp nut "${real_tty}" 2>/dev/null || true
    fi
    chmod 660 "${real_tty}" 2>/dev/null || true
  fi
done
shopt -u nullglob

exec /usr/bin/supervisord -c "${WEBNUT_SUPERVISOR_CONF:-/etc/supervisord.conf}"
