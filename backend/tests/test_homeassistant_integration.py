"""Tests for Home Assistant integration endpoints, auth, wake relay, and package zip."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.integrations import router as integrations_router
from app.database import utc_now_iso
from app.models.schemas import OrchestratorConfigModel, OrchestratorStateModel
from app.services.crypto import SecretBox
from app.services.event_log import EventLogService


class _StubOrchestrator:
    def __init__(self, telemetry: dict, state: OrchestratorStateModel, cfg: OrchestratorConfigModel) -> None:
        self.latest_telemetry = telemetry
        self._state = state
        self._cfg = cfg
        self.woken_hosts: list[tuple[int, bool]] = []

    async def get_state(self) -> OrchestratorStateModel:
        return self._state

    async def get_config(self) -> OrchestratorConfigModel:
        return self._cfg

    async def manual_wake_host(self, host_id: int, track_retries: bool = True) -> None:
        if host_id == 999:
            raise ValueError("Host 999 not found")
        self.woken_hosts.append((host_id, track_retries))


class _StubRepeater:
    def __init__(self, running: bool = True, clients: list[dict] | None = None) -> None:
        self._running = running
        self._clients = clients or []

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "port": 3493,
            "error": None,
            "mirrored_ups_count": 1,
            "virtual_ups_count": 1,
            "connected_clients": self._clients,
        }


class _StubVersionChecker:
    async def get_status(self):
        return type(
            "VerStatus",
            (),
            {
                "current_version": "2.2.0",
                "latest_version": "2.2.1",
                "update_available": True,
            },
        )()


def _build_ha_test_app(db, tmp_path: Path, telemetry: dict) -> tuple[FastAPI, _StubOrchestrator]:
    app = FastAPI()
    app.include_router(integrations_router)
    app.state.db = db
    app.state.secret_box = SecretBox(tmp_path)
    app.state.event_log = EventLogService(db)
    orch = _StubOrchestrator(
        telemetry=telemetry,
        state=OrchestratorStateModel(
            state="ON_BATTERY",
            last_ups_status="OB",
            stability_remaining_sec=0,
            disconnect_elapsed_sec=0,
            snapshot_frozen=True,
            is_dry_run=False,
            updated_at=utc_now_iso(),
        ),
        cfg=OrchestratorConfigModel(monitored_ups="apc_main", auto_recovery_enabled=True),
    )
    app.state.orchestrator = orch
    app.state.repeater = _StubRepeater(
        running=True,
        clients=[{"ups_name": "apc_main", "client_ip": "192.168.1.50", "username": "mon"}],
    )
    app.state.version_checker = _StubVersionChecker()
    return app, orch


@pytest.mark.asyncio
async def test_migration_v9_and_homeassistant_info_and_state(db, tmp_path: Path) -> None:
    row = await db.fetch_one("SELECT * FROM integration_settings WHERE id = 1")
    assert row is not None
    assert row["homeassistant_enabled"] == 1
    assert str(row["instance_id"]).strip() != ""

    now = utc_now_iso()
    host_id = await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, is_online, wake_state, virtual_ups_name, created_at, updated_at
        ) VALUES ('pve1', '192.168.1.50', 'AA:BB:CC:11:22:33', 1, 'IDLE', 'pve1', ?, ?)
        """,
        (now, now),
    )

    telemetry = {
        "connected": True,
        "error": None,
        "updated_at": now,
        "ups_list": [
            {
                "name": "apc_main",
                "description": "Main Rack UPS",
                "status": "OB",
                "status_tokens": ["OB"],
                "battery_charge": 76.0,
                "battery_runtime": 900.0,
                "ups_load": 30.0,
                "input_voltage": 0.0,
                "output_voltage": 230.2,
                "ups_mfr": "APC",
                "ups_model": "Smart-UPS 1500",
                "raw_vars": {
                    "battery.voltage": "26.8",
                    "ups.realpower.nominal": "1000",
                },
                "connected_clients": ["192.168.1.50"],
            }
        ],
    }

    app, orch = _build_ha_test_app(db, tmp_path, telemetry)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        info_res = await client.get("/api/integrations/homeassistant/info")
        assert info_res.status_code == 200
        info = info_res.json()
        assert info["instance_id"] == row["instance_id"]
        assert info["version"] == "2.2.0"
        assert info["api_key_required"] is False
        assert info["homeassistant_enabled"] is True

        state_res = await client.get("/api/integrations/homeassistant/state")
        assert state_res.status_code == 200
        state = state_res.json()
        assert state["connected"] is True
        assert state["version"] == "2.2.0"
        assert state["latest_version"] == "2.2.1"
        assert state["update_available"] is True
        assert state["orchestrator"]["state"] == "ON_BATTERY"
        assert state["orchestrator"]["state_label"] == "On Battery"
        assert state["hosts_online"] == 1
        assert state["hosts_total"] == 1
        assert state["clients_connected"] == 1
        assert len(state["ups"]) == 1
        ups = state["ups"][0]
        assert ups["name"] == "apc_main"
        assert ups["status_label"] == "On Battery"
        assert ups["on_battery"] is True
        assert ups["low_battery"] is False
        assert ups["battery_charge"] == 76.0
        assert ups["battery_runtime"] == 900
        assert ups["battery_voltage"] == 26.8
        assert ups["realpower_watts"] == 300.0
        assert len(state["hosts"]) == 1
        assert state["hosts"][0]["id"] == host_id
        assert state["hosts"][0]["virtual_ups_name"] == "pve1"

        wake_res = await client.post(f"/api/integrations/homeassistant/hosts/{host_id}/wake")
        assert wake_res.status_code == 200
        assert wake_res.json() == {"started": True, "host_id": host_id}
        assert orch.woken_hosts == [(host_id, True)]

        wake_missing = await client.post("/api/integrations/homeassistant/hosts/999/wake")
        assert wake_missing.status_code == 404


@pytest.mark.asyncio
async def test_homeassistant_auth_disable_and_package_zip(db, tmp_path: Path) -> None:
    app, _ = _build_ha_test_app(
        db,
        tmp_path,
        telemetry={"connected": False, "error": "Initializing", "ups_list": [], "updated_at": utc_now_iso()},
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Rotate API key -> HA endpoints enforce auth
        rot = await client.post("/api/integrations/api-key/rotate")
        api_key = rot.json()["api_key"]

        assert (await client.get("/api/integrations/homeassistant/info")).status_code == 401
        assert (await client.get("/api/integrations/homeassistant/state")).status_code == 401
        assert (await client.post("/api/integrations/homeassistant/hosts/1/wake")).status_code == 401
        assert (
            await client.get(
                "/api/integrations/homeassistant/state",
                headers={"X-API-Key": "wn_wrong"},
            )
        ).status_code == 401

        assert (
            await client.get(
                "/api/integrations/homeassistant/info",
                headers={"X-API-Key": api_key},
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/integrations/homeassistant/state",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        ).status_code == 200

        # Package ZIP contains manifest, brand icons, translations, and python modules
        pkg = await client.get("/api/integrations/homeassistant/package.zip")
        assert pkg.status_code == 200
        assert pkg.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(pkg.content), "r") as zf:
            names = set(zf.namelist())
            for expected in (
                "hacs.json",
                "custom_components/webnut/manifest.json",
                "custom_components/webnut/__init__.py",
                "custom_components/webnut/api.py",
                "custom_components/webnut/binary_sensor.py",
                "custom_components/webnut/button.py",
                "custom_components/webnut/config_flow.py",
                "custom_components/webnut/const.py",
                "custom_components/webnut/coordinator.py",
                "custom_components/webnut/entity.py",
                "custom_components/webnut/sensor.py",
                "custom_components/webnut/strings.json",
                "custom_components/webnut/translations/en.json",
                "custom_components/webnut/brand/icon.png",
                "custom_components/webnut/brand/icon@2x.png",
            ):
                assert expected in names

        # Disabling Home Assistant returns 403 even with valid API key
        upd = await client.put(
            "/api/integrations/settings",
            json={
                "api_key_enabled": True,
                "homepage_enabled": True,
                "homepage_default_ups": "",
                "homepage_refresh_ms": 10000,
                "homeassistant_enabled": False,
            },
        )
        assert upd.status_code == 200
        assert (
            await client.get(
                "/api/integrations/homeassistant/info",
                headers={"X-API-Key": api_key},
            )
        ).status_code == 403
        assert (
            await client.get(
                "/api/integrations/homeassistant/state",
                headers={"X-API-Key": api_key},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/integrations/homeassistant/hosts/1/wake",
                headers={"X-API-Key": api_key},
            )
        ).status_code == 403
