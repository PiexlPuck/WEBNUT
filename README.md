# WebNUT — UPS & Power Orchestrator (v2.0.0)

A self-hosted, Docker Compose-first power orchestration platform uniting **USB / Serial / SNMP / External NUT UPS setup**, a **Built-in Smart NUT Repeater**, **One-Button SSH/SFTP remote client deployment**, **per-host shutdown & staged Wake-on-LAN (WoL) recovery thresholds**, **UPS disconnection grace policies**, and **Pushover alerts**—all managed through a single dark-mode Web UI with zero CLI editing required.

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

## What's New in v2.0.0

- **Built-in Smart NUT Repeater (`0.0.0.0:3493`)**: Every remote client (`upsmon` / Proxmox / NAS / TrueNAS) points at WebNUT—whether your UPS is attached via local USB, Serial, SNMP, or an existing external NUT server. WebNUT exposes mirrored UPS streams plus per-host virtual UPS streams (e.g. `proxmox_1@<webnut-ip>`) so per-host shutdown battery `%`, on-battery delay, and disconnect grace policies apply live from the Web UI without editing client files.
- **One-Button SSH/SFTP Client Setup**: Enter the target host IP, SSH username/password (with optional encrypted storage in SQLite via Fernet), and click **Deploy Client via SSH**. WebNUT installs `nut-client`, uploads `nut.conf` and `upsmon.conf` pointing at WebNUT's Repeater, sets permissions, and enables `nut-monitor`.
- **Per-Host Shutdown & Staged Battery WoL Recovery**: Set per-host wake battery `%` (e.g. wake `Proxmox 1` at `10% OL` and `Proxmox 2` at `50% OL`), per-host shutdown battery `%`, on-battery delay, and optional SSH shutdown fallback (`nut_stream`, `ssh`, `both`, or `disabled`).
- **UPS Disconnection Grace Policy**: Choose globally (and override per host) whether to keep services running indefinitely on UPS comms loss (`keep_running`) or trigger shutdown after a configurable grace window (`shutdown_after_grace`, e.g. `10 minutes` / `600s`).
- **One-Page UPS Setup + SNMP Support**: Unified setup for USB, USB-Serial, SNMP (`v1`/`v2c`/`v3`, `nut-scanner -S` subnet discovery, MIB selection), and External NUT Server, with the raw 5-file editor cleanly tucked into a collapsible Advanced drawer.
- **Clearer UI & Branding**: Refreshed **WebNUT — UPS & Power Orchestrator** branding, custom battery-bolt SVG favicon, and intuitive sidebar navigation (`Overview`, `UPS Setup`, `Managed Hosts`, `Client Setup`, `Alerts`, `Activity Log`).

---

## Joining an Existing NUT Server (Smart Repeater Mode)

Already running `upsd` on a Synology, QNAP, Raspberry Pi, or another host? Open **UPS Setup → External NUT Repeater**, enter the upstream server IP/hostname and port (`3493`), click **Test Connection**, and click **Save & Connect**. WebNUT stops its local internal `upsd` (`127.0.0.1:3494`) and repeats all telemetry through WebNUT's **Smart NUT Repeater** on port `3493`—giving your LAN clients unified monitoring, per-host shutdown streaming, staged WoL recovery, and disconnect grace control.

---

## Architecture

Everything runs inside a single container managed by `supervisord` (`network_mode: host`):

- **`webnut` (FastAPI + Uvicorn + Compiled React UI)** on `WEB_PORT` (`8765`):
  - Serves the REST API (`/api/*`), live WebSocket (`/ws/live`), custom SVG favicon (`/favicon.svg`), and static frontend bundle.
  - Runs the **Smart NUT Repeater (`0.0.0.0:NUT_PORT`, default `3493`)** exposing mirrored upstream UPSes and per-host virtual UPS streams (`host_name@webnut`).
  - Connects to either local `upsd` (`127.0.0.1:WEBNUT_INTERNAL_NUT_PORT`, default `3494`) or an external upstream NUT server.
  - Atomically writes `/etc/nut/*` with timestamped backups (`.backups/`) and automatically rolls back if `upsdrvctl` / `upsd` / `upsmon` fails to start.
  - Persists managed hosts, encrypted SSH credentials (Fernet key at `/app/data/.secret.key`), pre-outage snapshots, recovery state, Pushover config, and audit logs in `/app/data/app.db` (SQLite).
- **`upsd` & UPS Drivers (`upsdrvctl` / `snmp-ups`)**: Started and reloaded safely via `supervisorctl` / `upsdrvctl` without mounting `/var/run/docker.sock`.
- **`upsmon`**: Local monitor running inside the container against configured `upsmon.conf`.

---

## Environment Variables & Volumes

| Variable | Default | Description |
|---|---|---|
| `TZ` | `UTC` | Container timezone |
| `WEB_PORT` | `8765` | Web UI & API port |
| `NUT_PORT` | `3493` | Public WebNUT Smart Repeater listen port for LAN clients |
| `WEBNUT_INTERNAL_NUT_PORT` | `3494` | Internal local `upsd` listen port (loopback only) |

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
