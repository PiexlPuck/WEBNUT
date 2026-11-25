# WEBNUT — Unified NUT & WOLNUT Web Manager

A self-hosted, Docker Compose-first web application that unifies **Network UPS Tools (NUT)** server/client configuration and live telemetry with **WOLNUT-style Wake-on-LAN (WoL) recovery orchestration**, an **automatic retry-on-failure verification loop**, and **Pushover notifications**—all managed through a single dark-mode Web UI with zero CLI editing required.

---

## Quickstart

### Option A: One-Line Interactive Installer (Recommended)

Run on your Linux Docker host—this clones WEBNUT, walks interactively through `.env` creation (`TZ`, `WEB_PORT`, `NUT_PORT`), pauses so you can plug in your USB or USB-Serial UPS adapter, configures Docker passthrough in `docker-compose.override.yml`, and builds/starts the stack:

```bash
curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash
```

### Option B: Manual Docker Compose Setup

1. Copy `.env.example` to `.env` and adjust `TZ`, `WEB_PORT` (default `8765`), and `NUT_PORT` (default `3493`) if needed:

   ```bash
   cp .env.example .env
   ```

2. Start the unified container:

   ```bash
   docker compose up -d --build
   ```

3. Open `http://<host-ip>:8765` in your browser.
   - On first startup, `/etc/nut` (`./data/nut`) is automatically seeded with valid default NUT configs (`nut.conf`, `ups.conf`, `upsd.conf`, `upsd.users`, `upsmon.conf`), random credentials, and `nut:nut` `0640` permissions.
   - Go to **NUT Config → Scan Hardware (USB & Serial)** to auto-detect USB UPSes, passed-through USB-Serial ports, and all `lsusb` devices in one place.

---

## Adding a USB or USB-Serial UPS Later

If you add a new USB-Serial UPS adapter after initial installation, run the standalone passthrough wizard on your Docker host:

```bash
./scripts/add-usb-serial.sh
```

It snapshots your current devices, prompts you to plug in the adapter, creates an optional persistent `udev` symlink (`/dev/webnut-ups-<alias>`), adds the passthrough mapping to `docker-compose.override.yml`, and recreates the container so the new port immediately appears under **NUT Config → Scan Hardware (USB & Serial)**.

---

## Updating WEBNUT

WEBNUT automatically checks GitHub for newer releases and can send a one-time Pushover alert when an update becomes available. The one-line installer also handles in-place updates (preserving `.env`, `docker-compose.override.yml`, and `./data/*`):

```bash
curl -fsSL https://raw.githubusercontent.com/PiexlPuck/WEBNUT/main/scripts/install.sh | bash -s -- --update
```

Or run manually from your WEBNUT directory:

```bash
git pull && docker compose up -d --build
```

---

## Joining an Existing NUT Server

Already running `upsd` on your host or another machine? Open **NUT Config → NUT Server Source → Join Existing NUT Server**, enter the server IP/hostname and port (`3493`), click **Test Connection**, and click **Save & Connect**. WEBNUT stops its built-in local `upsd`/`drivers` and runs all telemetry, WoL recovery, Pushover alerts, and client configuration directly against your existing NUT server.

---

## Architecture

Everything runs inside a single container managed by `supervisord` (`network_mode: host`):

- **`webnut` (FastAPI + Uvicorn + Compiled React UI)** on `WEB_PORT` (`8765`):
  - Serves the REST API (`/api/*`), live WebSocket (`/ws/live`), and static frontend bundle.
  - Polls `upsd` directly over the NUT TCP protocol (`127.0.0.1:NUT_PORT`).
  - Atomically writes `/etc/nut/*` with timestamped backups (`.backups/`) and automatically rolls back if `upsdrvctl` / `upsd` / `upsmon` fails to start.
  - Persists managed hosts, pre-outage snapshots, recovery state, Pushover config, and audit logs in `/app/data/app.db` (SQLite).
- **`upsd` & UPS Drivers (`upsdrvctl`)**: Started and reloaded safely via `supervisorctl` / `upsdrvctl` without mounting `/var/run/docker.sock`.
- **`upsmon`**: Monitors configured `MONITOR` entries in `upsmon.conf`.

---

## Environment Variables & Volumes

| Variable | Default | Description |
|---|---|---|
| `TZ` | `UTC` | Container timezone |
| `WEB_PORT` | `8765` | Web UI & API port |
| `NUT_PORT` | `3493` | NUT `upsd` listen/query port |

| Host Path | Container Path | Purpose |
|---|---|---|
| `./data/nut` | `/etc/nut` | Persistent NUT configuration & `.backups/` |
| `./data/app` | `/app/data` | Persistent SQLite database (`app.db`) |

---

## USB UPS Troubleshooting

1. **Device Not Found (`nut-scanner -U` returns 0 devices):**
   - Confirm the host kernel sees the UPS with `lsusb`.
   - Ensure `/dev/bus/usb:/dev/bus/usb` is mapped in `docker-compose.yml`.
   - If you unplug/replug the USB cable while the container is running, the USB bus device node changes; either restart the container (`docker compose restart`) or uncomment `privileged: true` in `docker-compose.yml`.
2. **USB Permission / `usbhid-ups` Claim Errors:**
   - Ensure no competing NUT instance (`nut-server` / `upsdrvctl`) is already running directly on the Docker host OS and claiming the USB HID interface.
   - Stop host-level NUT services (`sudo systemctl stop nut-server nut-client nut-driver.target`) if migrating to WEBNUT.
3. **Host OS Re-enumeration / `udev`:**
   - Some UPS units disconnect briefly when switching states; `ups.conf` defaults to `maxretry = 3`. Adding `pollinterval = 10` or matching `serial` / `vendorid` / `productid` helps when multiple UPS units are attached.

---

## BIOS / NIC Wake-on-LAN Prerequisites

For WEBNUT to reliably wake managed servers after utility power returns:

1. **BIOS / UEFI Settings:**
   - Enable **Wake-on-LAN (WoL)** / **Power On By PCI-E** / **Resume by LAN**.
   - Disable **ErP / EuP Ready** (ErP cuts standby power to the NIC during S5 shutdown, preventing WoL).
   - Optionally set **Restore on AC Power Loss** to **Power Off** (or **Last State**), allowing WEBNUT's battery threshold, stability hold, and staggered boot priority to orchestrate startup safely instead of tripping the UPS with simultaneous inrush current.
2. **OS & NIC Settings:**
   - **Linux:** Verify WoL is enabled (`ethtool eth0` should show `Wake-on: g`). Persist with `ethtool -s eth0 wol g` via NetworkManager, `systemd-networkd`, or `/etc/network/interfaces`.
   - **Windows:** Enable *Wake on Magic Packet* in Device Manager → Network Adapter → Advanced, and enable *Allow this device to wake the computer* under Power Management. Disable *Fast Startup* if S5 WoL behaves inconsistently.
3. **Network Requirements:**
   - `network_mode: host` is required so UDP port 9 magic packets (`255.255.255.255` or subnet-directed broadcast) and Layer 2 ARP inspection (`/proc/net/arp` / `ip neigh`) operate directly on the host LAN.
   - If hosts reside on a different VLAN/subnet, enter the host's **Subnet Broadcast** address (e.g. `192.168.20.255`) and ensure your router allows directed broadcasts.

---

## Development & Testing

```bash
# Backend unit & integration tests
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # or .venv/bin/pip on Linux/macOS
.venv/Scripts/pytest tests -v

# Frontend typecheck & production build
cd frontend
npm install
npx tsc --noEmit
npm run build
```
