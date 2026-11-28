"""Tests for Startup Onboarding Wizard API, instance naming, and Pushover/HA integration."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.integrations import router as integrations_router
from app.api.system import router as system_router
from app.config import settings
from app.database import Database, utc_now_iso
from app.models.schemas import PushoverConfigRequest
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.services.crypto import SecretBox
from app.services.event_log import EventLogService
from app.services.version_checker import VersionChecker
from app.services.ws_hub import WebSocketHub
from app.wol.orchestrator import RecoveryOrchestrator


def test_onboarding_and_instance_identity_endpoints(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "webnut_role", "main")
    db = Database(tmp_path / "onboarding.db")
    ws_hub = WebSocketHub()
    event_log = EventLogService(db, ws_hub=ws_hub)
    dispatcher = NotificationDispatcher(db, event_log)
    nut_manager = MagicMock()
    nut_manager.read_config.return_value = ({"ups_list": [{"name": "apc1"}]}, "")
    orchestrator = RecoveryOrchestrator(db, NutTcpClient(), dispatcher, event_log)
    orchestrator._latest_telemetry = {
        "connected": True,
        "ups_list": [{"name": "apc1", "status": "OL"}],
        "updated_at": utc_now_iso(),
    }
    version_checker = VersionChecker(db, dispatcher, event_log, current_version="2.5.0")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await db.connect()
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(system_router)
    app.include_router(integrations_router)
    app.state.db = db
    app.state.ws_hub = ws_hub
    app.state.event_log = event_log
    app.state.secret_box = SecretBox(tmp_path)
    app.state.nut_manager = nut_manager
    app.state.orchestrator = orchestrator
    app.state.version_checker = version_checker

    with TestClient(app) as client:
        # 1. Default onboarding state shows completed=False, skipped=False so wizard pops up
        ob = client.get("/api/system/onboarding").json()
        assert ob["completed"] is False
        assert ob["skipped"] is False
        assert ob["instance_name"] == "WebNUT"
        assert ob["has_ups_configured"] is True
        assert ob["has_ups_polling"] is True
        assert ob["has_hosts"] is False

        # 2. Rename instance updates /info and Home Assistant /homeassistant/info
        id_resp = client.put("/api/system/identity", json={"instance_name": "Garage Rack"})
        assert id_resp.status_code == 200
        assert id_resp.json()["instance_name"] == "Garage Rack"

        info = client.get("/api/system/info").json()
        assert info["instance_name"] == "Garage Rack"

        ha_info = client.get("/api/integrations/homeassistant/info").json()
        assert ha_info["name"] == "Garage Rack"

        # 3. Skip wizard persists skipped=True; reset brings it back
        skipped = client.put(
            "/api/system/onboarding",
            json={"skipped": True, "last_step": 2},
        ).json()
        assert skipped["skipped"] is True
        assert skipped["last_step"] == 2

        reset = client.post("/api/system/onboarding/reset").json()
        assert reset["skipped"] is False
        assert reset["completed"] is False
        assert reset["last_step"] == 0
        assert reset["instance_name"] == "Garage Rack"

        # 4. Complete wizard
        done = client.put(
            "/api/system/onboarding",
            json={"completed": True, "last_step": 5},
        ).json()
        assert done["completed"] is True
        assert done["completed_at"] is not None


@pytest.mark.asyncio
async def test_dispatcher_prefixes_custom_instance_name(db: Database, event_log: EventLogService):
    sent_bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(request.content.decode("utf-8"))
        return httpx.Response(200, json={"status": 1, "request": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        dispatcher = NotificationDispatcher(db, event_log, http_client=client)
        await dispatcher.update_pushover_config(
            PushoverConfigRequest(enabled=True, user_key="u1", api_token="t1")
        )

        # Default name "WebNUT" -> title unchanged
        await dispatcher.dispatch_event("ups_on_battery", "Power Outage", "UPS OB")
        assert "title=Power+Outage" in sent_bodies[0]

        # Custom name "Garage UPS" -> title prefixed cleanly
        await db.execute(
            "UPDATE onboarding_state SET instance_name = 'Garage UPS' WHERE id = 1"
        )
        await dispatcher.dispatch_event("ups_on_battery", "Power Outage", "UPS OB")
        assert "title=%5BGarage+UPS%5D+Power+Outage" in sent_bodies[1]
