"""Tests for Grafana dashboard JSON and Prometheus /metrics exposition."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.integrations import get_prometheus_metrics, router as integrations_router
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
    def get_status(self) -> dict:
        return {
            "running": True,
            "port": 3493,
            "error": None,
            "mirrored_ups_count": 1,
            "virtual_ups_count": 1,
            "connected_clients": [
                {"ups_name": "apc_main", "client_ip": "192.168.1.50", "username": "mon"}
            ],
        }


def _build_prom_test_app(db, tmp_path: Path, telemetry: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router)
    app.add_api_route("/metrics", get_prometheus_metrics, methods=["GET"])
    app.state.db = db
    app.state.secret_box = SecretBox(tmp_path)
    app.state.event_log = EventLogService(db)
    app.state.orchestrator = _StubOrchestrator(
        telemetry=telemetry,
        state=OrchestratorStateModel(
            state="STABILIZING",
            last_ups_status="OL",
            stability_remaining_sec=42,
            disconnect_elapsed_sec=0,
            snapshot_frozen=True,
            is_dry_run=False,
            updated_at=utc_now_iso(),
        ),
        cfg=OrchestratorConfigModel(monitored_ups="apc_main", auto_recovery_enabled=True),
    )
    app.state.repeater = _StubRepeater()
    return app


@pytest.mark.asyncio
async def test_prometheus_metrics_and_root_alias(db, tmp_path: Path) -> None:
    row = await db.fetch_one("SELECT * FROM integration_settings WHERE id = 1")
    assert row is not None
    assert row["prometheus_enabled"] == 1

    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, is_online, wake_state, created_at, updated_at
        ) VALUES ('rack "pve1"', '192.168.1.50', 'AA:BB:CC:11:22:33', 1, 'IDLE', ?, ?)
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
                "status": "OL",
                "status_tokens": ["OL"],
                "battery_charge": 98.0,
                "battery_runtime": 2100.0,
                "ups_load": 22.0,
                "input_voltage": 230.5,
                "output_voltage": 230.0,
                "ups_mfr": "APC",
                "ups_model": "Smart-UPS 1500",
                "raw_vars": {"ups.realpower": "220", "battery.voltage": "27.1"},
                "connected_clients": ["192.168.1.50"],
            }
        ],
    }

    app = _build_prom_test_app(db, tmp_path, telemetry)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/metrics", "/api/integrations/prometheus/metrics"):
            res = await client.get(path)
            assert res.status_code == 200
            assert "text/plain" in res.headers["content-type"]
            body = res.text
            assert "webnut_info{" in body
            assert 'webnut_ups_battery_charge_percent{ups="apc_main"} 98.0' in body
            assert 'webnut_ups_realpower_watts{ups="apc_main"} 220.0' in body
            assert 'webnut_orchestrator_state{state="stabilizing"} 1' in body
            assert 'webnut_orchestrator_state{state="normal"} 0' in body
            assert "webnut_orchestrator_stability_remaining_seconds 42" in body
            assert r'webnut_host_online{host="rack \"pve1\"",ip="192.168.1.50"} 1' in body


@pytest.mark.asyncio
async def test_prometheus_auth_disable_scrape_yaml_and_grafana_dashboard(db, tmp_path: Path) -> None:
    telemetry = {
        "connected": True,
        "error": None,
        "updated_at": utc_now_iso(),
        "ups_list": [{"name": "apc_main", "status": "OL", "status_tokens": ["OL"], "raw_vars": {}}],
    }
    app = _build_prom_test_app(db, tmp_path, telemetry)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rot = await client.post("/api/integrations/api-key/rotate")
        api_key = rot.json()["api_key"]

        # Auth enforced on /metrics and /api/integrations/prometheus/metrics
        assert (await client.get("/metrics")).status_code == 401
        assert (await client.get("/api/integrations/prometheus/metrics")).status_code == 401
        assert (
            await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (
            await client.get("/metrics", headers={"Authorization": f"Bearer {api_key}"})
        ).status_code == 200
        assert (
            await client.get(
                "/api/integrations/prometheus/metrics",
                headers={"X-API-Key": api_key},
            )
        ).status_code == 200

        # Scrape YAML generator (masked vs revealed) + injection rejection
        gen_masked = await client.post(
            "/api/integrations/prometheus/generate",
            json={
                "job_name": "webnut-prod",
                "target_host": "http://192.168.1.10:8765/",
                "scheme": "http",
                "scrape_interval_sec": 15,
                "metrics_path": "/metrics",
                "reveal_api_key": False,
            },
        )
        assert gen_masked.status_code == 200
        m_body = gen_masked.json()
        assert m_body["metrics_url"] == "http://192.168.1.10:8765/metrics"
        assert m_body["requires_api_key"] is True
        assert 'credentials: "YOUR_WEBNUT_API_KEY"' in m_body["scrape_yaml"]
        assert api_key not in m_body["scrape_yaml"]
        assert '- targets: ["192.168.1.10:8765"]' in m_body["scrape_yaml"]

        gen_revealed = await client.post(
            "/api/integrations/prometheus/generate",
            json={
                "job_name": "webnut",
                "target_host": "192.168.1.10:8765",
                "scheme": "https",
                "scrape_interval_sec": 30,
                "metrics_path": "/api/integrations/prometheus/metrics",
                "reveal_api_key": True,
            },
        )
        assert gen_revealed.status_code == 200
        assert f'credentials: "{api_key}"' in gen_revealed.json()["scrape_yaml"]

        bad = await client.post(
            "/api/integrations/prometheus/generate",
            json={"job_name": "bad\nevil: 1", "target_host": "192.168.1.10:8765"},
        )
        assert bad.status_code == 422

        # Grafana Dashboard JSON download
        dash_res = await client.get("/api/integrations/grafana/dashboard.json")
        assert dash_res.status_code == 200
        assert "application/json" in dash_res.headers["content-type"]
        dash = dash_res.json()
        assert dash["uid"] == "webnut-power-overview"
        assert len(dash["panels"]) >= 10
        assert any(v["name"] == "ups" for v in dash["templating"]["list"])
        assert any(i["name"] == "DS_PROMETHEUS" for i in dash["__inputs"])

        # Disabling prometheus_enabled returns 403
        upd = await client.put(
            "/api/integrations/settings",
            json={
                "api_key_enabled": True,
                "homepage_enabled": True,
                "homepage_default_ups": "",
                "homepage_refresh_ms": 10000,
                "homeassistant_enabled": True,
                "prometheus_enabled": False,
            },
        )
        assert upd.status_code == 200
        assert (
            await client.get("/metrics", headers={"Authorization": f"Bearer {api_key}"})
        ).status_code == 403
