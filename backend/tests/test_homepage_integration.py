"""Tests for Homepage integration summary endpoint, API key auth, and YAML generator."""

from __future__ import annotations

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

    async def get_state(self) -> OrchestratorStateModel:
        return self._state

    async def get_config(self) -> OrchestratorConfigModel:
        return self._cfg


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


class _StubNutManager:
    def __init__(self, ups_names: list[str] | None = None) -> None:
        self._ups_names = ups_names or []

    def read_config(self, filename: str, mask_secrets: bool = True):
        if filename == "ups.conf":
            return {"ups_list": [{"name": n} for n in self._ups_names]}, ""
        return {}, ""


def _build_test_app(
    db,
    tmp_path: Path,
    telemetry: dict,
    state: OrchestratorStateModel | None = None,
    cfg: OrchestratorConfigModel | None = None,
    repeater: _StubRepeater | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router)
    app.state.db = db
    app.state.secret_box = SecretBox(tmp_path)
    app.state.event_log = EventLogService(db)
    app.state.orchestrator = _StubOrchestrator(
        telemetry=telemetry,
        state=state
        or OrchestratorStateModel(
            state="NORMAL",
            last_ups_status="OL",
            snapshot_frozen=False,
            is_dry_run=False,
            updated_at=utc_now_iso(),
        ),
        cfg=cfg or OrchestratorConfigModel(monitored_ups="apc_main"),
    )
    app.state.repeater = repeater or _StubRepeater(
        running=True,
        clients=[
            {"ups_name": "apc_main", "client_ip": "192.168.1.50", "username": "mon"},
            {"ups_name": "pve1", "client_ip": "192.168.1.51", "username": "mon"},
        ],
    )
    app.state.nut_manager = _StubNutManager(["apc_main", "eaton_rack"])
    return app


async def _seed_hosts(db) -> None:
    now = utc_now_iso()
    hosts = [
        ("pve1", "192.168.1.50", "AA:BB:CC:11:22:33", 1, "IDLE"),
        ("nas1", "192.168.1.51", "AA:BB:CC:11:22:44", 0, "WAKING"),
        ("win1", "192.168.1.52", "AA:BB:CC:11:22:55", 1, "IDLE"),
    ]
    for name, ip, mac, online, wake_state in hosts:
        await db.execute(
            """
            INSERT INTO hosts (
                name, ip_or_host, mac_address, is_online, wake_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, ip, mac, online, wake_state, now, now),
        )


@pytest.mark.asyncio
async def test_migration_v8_defaults_and_summary_payload(db, tmp_path: Path) -> None:
    row = await db.fetch_one("SELECT * FROM integration_settings WHERE id = 1")
    assert row is not None
    assert row["api_key_enabled"] == 0
    assert row["homepage_enabled"] == 1
    assert row["homepage_refresh_ms"] == 10000

    await _seed_hosts(db)
    telemetry = {
        "connected": True,
        "error": None,
        "updated_at": "2026-11-25T10:00:00+00:00",
        "ups_list": [
            {
                "name": "apc_main",
                "description": "Main Rack UPS",
                "status": "OL CHRG",
                "status_tokens": ["OL", "CHRG"],
                "battery_charge": 92.0,
                "battery_runtime": 1800.0,
                "ups_load": 25.0,
                "input_voltage": 231.4,
                "output_voltage": 230.0,
                "ups_mfr": "APC",
                "ups_model": "Smart-UPS 1500",
                "raw_vars": {"ups.realpower.nominal": "1000"},
                "connected_clients": ["192.168.1.50", "192.168.1.60"],
            },
            {
                "name": "eaton_rack",
                "description": "Secondary UPS",
                "status": "OB LB",
                "status_tokens": ["OB", "LB"],
                "battery_charge": 14.0,
                "battery_runtime": 240.0,
                "ups_load": 40.0,
                "input_voltage": 0.0,
                "output_voltage": 230.0,
                "ups_mfr": "Eaton",
                "ups_model": "Eaton 5PX",
                "raw_vars": {"ups.realpower": "412"},
                "connected_clients": [],
            },
        ],
    }

    app = _build_test_app(
        db,
        tmp_path,
        telemetry=telemetry,
        state=OrchestratorStateModel(
            state="STABILIZING",
            last_ups_status="OL",
            snapshot_frozen=True,
            is_dry_run=False,
            updated_at=utc_now_iso(),
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/integrations/homepage/summary")
        assert res.status_code == 200
        data = res.json()
        assert data["instance_name"] == "WebNUT"
        assert data["connected"] is True
        assert data["ups_name"] == "apc_main"
        assert data["ups_model"] == "APC Smart-UPS 1500"
        assert data["ups_status"] == "OL CHRG"
        assert data["ups_status_label"] == "Online (Charging)"
        assert data["battery_charge"] == 92.0
        assert data["ups_load"] == 25.0
        assert data["battery_runtime_sec"] == 1800
        assert data["battery_runtime_min"] == 30.0
        # 1000W nominal * 25% load = 250.0W
        assert data["realpower_watts"] == 250.0
        assert data["orchestrator_state"] == "STABILIZING"
        assert data["orchestrator_state_label"] == "Stabilizing"
        assert data["hosts_online"] == 2
        assert data["hosts_total"] == 3
        assert data["hosts_summary"] == "2/3"
        assert data["hosts_waking"] == 1
        assert data["repeater_running"] is True
        # (apc_main, .50) deduplicated across repeater + ups_list, plus (pve1, .51) and (apc_main, .60)
        assert data["clients_connected"] == 3
        assert data["ups_count"] == 2

        # Select second UPS explicitly (uses direct ups.realpower)
        res2 = await client.get("/api/integrations/homepage/summary?ups=eaton_rack")
        assert res2.status_code == 200
        d2 = res2.json()
        assert d2["ups_name"] == "eaton_rack"
        assert d2["ups_status_label"] == "Low Battery"
        assert d2["on_battery"] is True
        assert d2["low_battery"] is True
        assert d2["realpower_watts"] == 412.0

        # Unknown UPS returns clean NOT_FOUND payload
        res3 = await client.get("/api/integrations/homepage/summary?ups=missing_ups")
        assert res3.status_code == 200
        d3 = res3.json()
        assert d3["connected"] is False
        assert d3["ups_status"] == "NOT_FOUND"
        assert d3["ups_status_label"] == "UPS Not Found"

        # Disabling homepage endpoint returns 403 on summary
        upd = await client.put(
            "/api/integrations/settings",
            json={
                "api_key_enabled": False,
                "homepage_enabled": False,
                "homepage_default_ups": "",
                "homepage_refresh_ms": 10000,
            },
        )
        assert upd.status_code == 200
        disabled_res = await client.get("/api/integrations/homepage/summary")
        assert disabled_res.status_code == 403

        # Re-enable and verify custom instance_name flows through settings and summary
        await client.put(
            "/api/integrations/settings",
            json={
                "api_key_enabled": False,
                "homepage_enabled": True,
                "homepage_default_ups": "",
                "homepage_refresh_ms": 10000,
            },
        )
        await db.execute("UPDATE onboarding_state SET instance_name = 'Rack Monitor' WHERE id = 1")
        cfg_view = (await client.get("/api/integrations/settings")).json()
        assert cfg_view["instance_name"] == "Rack Monitor"
        summary_view = (await client.get("/api/integrations/homepage/summary")).json()
        assert summary_view["instance_name"] == "Rack Monitor"


@pytest.mark.asyncio
async def test_api_key_rotation_auth_and_revocation(db, tmp_path: Path) -> None:
    telemetry = {
        "connected": True,
        "error": None,
        "updated_at": utc_now_iso(),
        "ups_list": [{"name": "apc_main", "status": "OL", "status_tokens": ["OL"], "raw_vars": {}}],
    }
    app = _build_test_app(db, tmp_path, telemetry=telemetry)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rot = await client.post("/api/integrations/api-key/rotate")
        assert rot.status_code == 200
        key_data = rot.json()
        api_key = key_data["api_key"]
        assert api_key.startswith("wn_")
        assert key_data["api_key_enabled"] is True

        # Stored encrypted in SQLite, not plaintext
        db_row = await db.fetch_one("SELECT * FROM integration_settings WHERE id = 1")
        assert db_row is not None
        assert db_row["api_key_enc"]
        assert api_key not in str(db_row["api_key_enc"])

        # Reveal endpoint returns matching key
        rev = await client.get("/api/integrations/api-key/reveal")
        assert rev.status_code == 200
        assert rev.json()["api_key"] == api_key

        # Missing or wrong key -> 401
        r_missing = await client.get("/api/integrations/homepage/summary")
        assert r_missing.status_code == 401
        r_wrong = await client.get(
            "/api/integrations/homepage/summary", headers={"X-API-Key": "wn_wrong"}
        )
        assert r_wrong.status_code == 401

        # Valid X-API-Key and Bearer -> 200
        r_header = await client.get(
            "/api/integrations/homepage/summary", headers={"X-API-Key": api_key}
        )
        assert r_header.status_code == 200
        r_bearer = await client.get(
            "/api/integrations/homepage/summary",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r_bearer.status_code == 200

        # Revoke disables auth and allows open access again
        d_res = await client.delete("/api/integrations/api-key")
        assert d_res.status_code == 200
        assert d_res.json()["revoked"] is True

        r_open = await client.get("/api/integrations/homepage/summary")
        assert r_open.status_code == 200


@pytest.mark.asyncio
async def test_homepage_yaml_generator_presets_and_validation(db, tmp_path: Path) -> None:
    telemetry = {
        "connected": True,
        "error": None,
        "updated_at": utc_now_iso(),
        "ups_list": [
            {
                "name": "apc_main",
                "status": "OL",
                "status_tokens": ["OL"],
                "battery_charge": 100.0,
                "ups_load": 15.0,
                "battery_runtime": 2400.0,
                "raw_vars": {},
            }
        ],
    }
    app = _build_test_app(db, tmp_path, telemetry=telemetry)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Enable API key first
        rot = await client.post("/api/integrations/api-key/rotate")
        api_key = rot.json()["api_key"]

        # Detailed preset with list display and masked key placeholder
        gen = await client.post(
            "/api/integrations/homepage/generate",
            json={
                "service_name": "Rack UPS",
                "description": "Primary UPS",
                "icon": "mdi-battery-charging",
                "base_url": "http://192.168.1.10:8765/",
                "href_url": "",
                "ups_name": "apc_main",
                "preset": "detailed",
                "display": "list",
                "selected_fields": [],
                "refresh_interval_ms": 15000,
                "reveal_api_key": False,
            },
        )
        assert gen.status_code == 200
        body = gen.json()
        yaml_txt = body["yaml"]
        assert body["endpoint_url"] == "http://192.168.1.10:8765/api/integrations/homepage/summary?ups=apc_main"
        assert body["requires_api_key"] is True
        assert "type: customapi" in yaml_txt
        assert "display: list" in yaml_txt
        assert 'X-API-Key: "YOUR_WEBNUT_API_KEY"' in yaml_txt
        assert api_key not in yaml_txt
        assert "field: realpower_watts" in yaml_txt

        # Custom preset with revealed API key
        gen_custom = await client.post(
            "/api/integrations/homepage/generate",
            json={
                "service_name": "WebNUT",
                "description": "UPS",
                "icon": "mdi-flash",
                "base_url": "http://192.168.1.10:8765",
                "href_url": "https://ups.home.arpa",
                "ups_name": "",
                "preset": "custom",
                "display": "block",
                "selected_fields": ["ups_status_label", "hosts_summary"],
                "refresh_interval_ms": 5000,
                "reveal_api_key": True,
            },
        )
        assert gen_custom.status_code == 200
        custom_yaml = gen_custom.json()["yaml"]
        assert f'X-API-Key: "{api_key}"' in custom_yaml
        assert 'href: "https://ups.home.arpa"' in custom_yaml
        assert "field: hosts_summary" in custom_yaml
        assert "display: list" not in custom_yaml

        # Reject newline / YAML injection
        bad = await client.post(
            "/api/integrations/homepage/generate",
            json={
                "service_name": "WebNUT\n    evil: true",
                "base_url": "http://192.168.1.10:8765",
            },
        )
        assert bad.status_code == 422

        bad_url = await client.post(
            "/api/integrations/homepage/generate",
            json={
                "service_name": "WebNUT",
                "base_url": "ftp://192.168.1.10:8765",
            },
        )
        assert bad_url.status_code == 422
