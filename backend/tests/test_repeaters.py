"""Tests for WEBNUT Repeater Node pairing, WS tunnel, namespacing, RPC, and disconnect grace."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.api.control import router as control_router
from app.api.nut import router as nut_router
from app.api.repeaters import router as repeaters_router
from app.api.system import router as system_router
from app.config import settings
from app.database import Database, utc_now_iso
from app.models.schemas import RepeaterJoinRequest, RepeaterNodeUpdateRequest
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.nut.repeater import NutRepeaterServer
from app.services.crypto import SecretBox
from app.services.event_log import EventLogService
from app.services.repeater_agent import RepeaterAgent
from app.services.repeater_hub import RepeaterHub, namespace_ups_name
from app.services.selftest_scheduler import SelfTestScheduler
from app.services.ws_hub import WebSocketHub
from app.wol.orchestrator import RecoveryOrchestrator


@pytest.mark.asyncio
async def test_join_tokens_uniqueness_expiry_revocation(db: Database, event_log: EventLogService):
    dispatcher = NotificationDispatcher(db, event_log)
    hub = RepeaterHub(db, event_log, dispatcher)
    await hub.start()

    try:
        # 1. Create single-use token & verify redemption + slug uniqueness
        created = await hub.create_join_token(label="Garage", expires_hours=24, max_uses=2)
        assert created.token.startswith("wnjt_")
        assert "WEBNUT_ROLE=repeater" in created.docker_compose_yaml
        assert created.token in created.docker_compose_yaml

        res1 = await hub.redeem_join_token(
            RepeaterJoinRequest(join_token=created.token, node_name="Garage UPS", app_version="2.3.0"),
            remote_ip="192.168.1.51",
        )
        assert res1.node_slug == "garage_ups"
        assert res1.auth_token.startswith("wnrt_")

        res2 = await hub.redeem_join_token(
            RepeaterJoinRequest(join_token=created.token, node_name="Garage UPS", app_version="2.3.0"),
            remote_ip="192.168.1.52",
        )
        assert res2.node_slug == "garage_ups_2"

        # Max uses reached
        with pytest.raises(PermissionError, match="maximum uses"):
            await hub.redeem_join_token(
                RepeaterJoinRequest(join_token=created.token, node_name="Garage UPS 3")
            )

        # 2. Expired token rejection
        exp_tok = await hub.create_join_token(label="Old", expires_hours=1, max_uses=1)
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await db.execute(
            "UPDATE repeater_join_tokens SET expires_at = ? WHERE id = ?",
            (past, exp_tok.token_record.id),
        )
        with pytest.raises(PermissionError, match="expired"):
            await hub.redeem_join_token(
                RepeaterJoinRequest(join_token=exp_tok.token, node_name="Late Node")
            )

        # 3. Revoked token rejection
        rev_tok = await hub.create_join_token(label="Revoked", expires_hours=24, max_uses=5)
        assert await hub.revoke_join_token(rev_tok.token_record.id) is True
        with pytest.raises(PermissionError, match="revoked"):
            await hub.redeem_join_token(
                RepeaterJoinRequest(join_token=rev_tok.token, node_name="Revoked Node")
            )

        assert namespace_ups_name("garage_ups", "apc-1500") == "garage_ups_apc-1500"
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_offline_repeater_triggers_notification_and_disconnect_grace(
    db: Database, event_log: EventLogService
):
    dispatcher = NotificationDispatcher(db, event_log)
    dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]
    nut_client = NutTcpClient(host="127.0.0.1", port=34999)
    nut_client.fetch_telemetry = AsyncMock()  # type: ignore[method-assign]

    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    hub = RepeaterHub(db, event_log, dispatcher)
    orchestrator = RecoveryOrchestrator(
        db,
        nut_client,
        dispatcher,
        event_log,
        repeater=repeater,
        repeater_hub=hub,
    )
    hub.set_on_telemetry_change(orchestrator.refresh_from_repeaters)
    await hub.start()

    try:
        # Pair a node & simulate cached last telemetry before going offline
        tok = await hub.create_join_token(label="RackB", expires_hours=1, max_uses=1)
        joined = await hub.redeem_join_token(
            RepeaterJoinRequest(join_token=tok.token, node_name="rackb"),
            remote_ip="10.0.0.20",
        )
        remote_ups_name = namespace_ups_name(joined.node_slug, "apc")
        now = utc_now_iso()
        await db.execute(
            "UPDATE repeater_nodes SET last_ups_json = ? WHERE id = ?",
            (
                f'[{{"name": "{remote_ups_name}", "remote_ups_name": "apc", "description": "RackB APC", '
                f'"status": "OL", "status_tokens": ["OL"], "battery_charge": 100.0, '
                f'"source_node_id": {joined.node_id}, "source_node_name": "rackb", '
                f'"source_node_slug": "{joined.node_slug}", "node_online": true, "raw_vars": {{"ups.status": "OL"}}}}]',
                joined.node_id,
            ),
        )
        await hub.refresh_cache()

        # Host 1: bound to remote UPS with keep_running
        await db.execute(
            """
            INSERT INTO hosts (
                name, ip_or_host, mac_address, nut_ups_name, virtual_ups_name,
                disconnect_policy, disconnect_grace_sec, enabled, is_online,
                was_online_before_outage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'keep_running', 5, 1, 1, 1, ?, ?)
            """,
            ("nas-keep", "10.0.0.5", "AA:BB:CC:DD:EE:01", remote_ups_name, "v_keep", now, now),
        )
        # Host 2: bound to remote UPS with shutdown_after_grace (5s)
        await db.execute(
            """
            INSERT INTO hosts (
                name, ip_or_host, mac_address, nut_ups_name, virtual_ups_name,
                disconnect_policy, disconnect_grace_sec, enabled, is_online,
                was_online_before_outage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'shutdown_after_grace', 5, 1, 1, 1, ?, ?)
            """,
            ("nas-grace", "10.0.0.6", "AA:BB:CC:DD:EE:02", remote_ups_name, "v_grace", now, now),
        )
        # Host 3: bound to healthy local ups1 (OL) -> should stay untouched OL
        await db.execute(
            """
            INSERT INTO hosts (
                name, ip_or_host, mac_address, nut_ups_name, virtual_ups_name,
                disconnect_policy, disconnect_grace_sec, enabled, is_online,
                was_online_before_outage, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'shutdown_after_grace', 1, 1, 1, 1, ?, ?)
            """,
            ("local-box", "10.0.0.7", "AA:BB:CC:DD:EE:03", "ups1", "v_local", now, now),
        )

        local_snap = {
            "connected": True,
            "error": None,
            "updated_at": now,
            "ups_list": [
                {
                    "name": "ups1",
                    "description": "Local UPS",
                    "status": "OL",
                    "status_tokens": ["OL"],
                    "battery_charge": 100.0,
                    "raw_vars": {"ups.status": "OL"},
                }
            ],
        }

        # Initial evaluation with offline Repeater (node has no active WS session)
        merged = orchestrator._build_merged_snapshot(local_snap)
        rack_ups = [u for u in merged["ups_list"] if u["name"] == remote_ups_name][0]
        assert rack_ups["node_online"] is False
        assert rack_ups["status"] == "OFFLINE NOCOMM"

        await orchestrator.evaluate_telemetry(merged)

        # Within grace window: both remote hosts held at OL; local host is OL
        assert repeater._resolve_ups("v_keep")[1]["ups.status"] == "OL"
        assert repeater._resolve_ups("v_grace")[1]["ups.status"] == "OL"
        assert repeater._resolve_ups("v_local")[1]["ups.status"] == "OL"

        # Fast-forward disconnect start for remote UPS beyond 5s grace
        orchestrator._ups_disconnect_started_at[remote_ups_name] = datetime.now(
            timezone.utc
        ) - timedelta(seconds=10)
        await orchestrator.evaluate_telemetry(orchestrator._build_merged_snapshot(local_snap))

        # keep_running stays OL; shutdown_after_grace receives OB LB FSD; local_box unaffected
        assert repeater._resolve_ups("v_keep")[1]["ups.status"] == "OL"
        assert repeater._resolve_ups("v_grace")[1]["ups.status"] == "OB LB FSD"
        assert repeater._resolve_ups("v_local")[1]["ups.status"] == "OL"
    finally:
        await hub.stop()


def test_websocket_tunnel_auth_telemetry_rpc_and_offline_event(tmp_path: Path):
    """End-to-end TestClient test for /api/repeaters, /ws/repeater, and disconnect."""
    db = Database(tmp_path / "ws_test.db")
    ws_hub = WebSocketHub()
    event_log = EventLogService(db, ws_hub=ws_hub)
    dispatcher = NotificationDispatcher(db, event_log)
    dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]
    nut_client = NutTcpClient(host="127.0.0.1", port=34999)
    nut_manager = MagicMock()
    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    hub = RepeaterHub(db, event_log, dispatcher, ws_hub=ws_hub)
    orchestrator = RecoveryOrchestrator(
        db,
        nut_client,
        dispatcher,
        event_log,
        ws_hub=ws_hub,
        repeater=repeater,
        repeater_hub=hub,
    )
    hub.set_on_telemetry_change(orchestrator.refresh_from_repeaters)
    scheduler = SelfTestScheduler(
        db, nut_client, nut_manager, orchestrator, dispatcher, event_log, repeater_hub=hub
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await db.connect()
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()
            await db.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(repeaters_router)
    app.include_router(control_router)
    app.include_router(system_router)
    app.state.db = db
    app.state.ws_hub = ws_hub
    app.state.event_log = event_log
    app.state.dispatcher = dispatcher
    app.state.nut_client = nut_client
    app.state.nut_manager = nut_manager
    app.state.repeater = repeater
    app.state.repeater_hub = hub
    app.state.orchestrator = orchestrator
    app.state.selftest_scheduler = scheduler

    @app.websocket("/ws/repeater")
    async def ws_repeater(websocket: WebSocket):
        await hub.handle_websocket(websocket)

    with TestClient(app) as client:
        # 1. Create token & pair via REST API
        tok_resp = client.post(
            "/api/repeaters/tokens",
            json={"label": "shed", "expires_hours": 12, "max_uses": 1},
        )
        assert tok_resp.status_code == 200
        tok = tok_resp.json()["token"]

        join_resp = client.post(
            "/api/repeaters/join",
            json={"join_token": tok, "node_name": "Shed Pi", "app_version": "2.3.0"},
        )
        assert join_resp.status_code == 200
        joined = join_resp.json()
        auth_token = joined["auth_token"]
        node_id = joined["node_id"]
        assert joined["node_slug"] == "shed_pi"

        # 2. Invalid WS token rejected with close code 4001
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/repeater?token=invalid-token") as bad_ws:
                bad_ws.receive_text()

        # 3. Connect valid Repeater WS, push telemetry & test live nodes / Smart Repeater
        with client.websocket_connect(
            "/ws/repeater",
            headers={"Authorization": f"Bearer {auth_token}"},
        ) as ws:
            ws.send_json({"type": "hello", "app_version": "2.3.0", "ui_url": "http://10.0.0.88:8765"})
            ws.send_json(
                {
                    "type": "telemetry",
                    "data": {
                        "connected": True,
                        "ups_list": [
                            {
                                "name": "cyber",
                                "description": "Shed CyberPower",
                                "status": "OL",
                                "status_tokens": ["OL"],
                                "battery_charge": 98.0,
                                "ups_load": 14.0,
                                "raw_vars": {"ups.status": "OL", "battery.charge": "98"},
                            }
                        ],
                    },
                }
            )
            ws.send_json({"type": "heartbeat", "ts": "123.4", "latency_ms": 4.2})
            ack = ws.receive_json()
            assert ack["type"] == "heartbeat_ack"

            nodes = client.get("/api/repeaters/nodes").json()
            assert len(nodes) == 1
            assert nodes[0]["online"] is True
            assert nodes[0]["ups_names"] == ["shed_pi_cyber"]
            assert repeater._resolve_ups("shed_pi_cyber") is not None

        # Wait briefly for async disconnect cleanup in handle_websocket's finally block
        for _ in range(25):
            ups_list = orchestrator.latest_telemetry.get("ups_list") or []
            if ups_list and ups_list[0].get("node_online") is False:
                break
            time.sleep(0.02)

        nodes_after = client.get("/api/repeaters/nodes").json()
        assert nodes_after[0]["online"] is False
        assert orchestrator.latest_telemetry["ups_list"][0]["status"] == "OFFLINE NOCOMM"
        assert orchestrator.latest_telemetry["ups_list"][0]["node_online"] is False
        dispatcher.dispatch_event.assert_any_call(
            "repeater_node_offline",
            "Repeater Node Offline: Shed Pi",
            "Repeater node 'Shed Pi' disconnected. Cached UPSes marked OFFLINE NOCOMM and host disconnect policies are active.",
            {"node_id": node_id, "slug": "shed_pi"},
        )

        # Delete node removes it completely
        del_resp = client.delete(f"/api/repeaters/nodes/{node_id}")
        assert del_resp.status_code == 200
        assert client.get("/api/repeaters/nodes").json() == []


@pytest.mark.asyncio
async def test_repeater_hub_rpc_roundtrip_over_mock_ws(
    tmp_path: Path, db: Database, event_log: EventLogService
):
    dispatcher = NotificationDispatcher(db, event_log)
    dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]
    hub = RepeaterHub(db, event_log, dispatcher)
    await hub.start()

    try:
        tok = await hub.create_join_token(label="Office", expires_hours=1, max_uses=1)
        joined = await hub.redeem_join_token(
            RepeaterJoinRequest(join_token=tok.token, node_name="Office"),
            remote_ip="10.0.0.30",
        )
        updated = await hub.update_node(
            joined.node_id,
            RepeaterNodeUpdateRequest(name="Office Node", ui_url="http://10.0.0.30:8765"),
        )
        assert updated is not None
        assert updated.name == "Office Node"

        # Prepare RepeaterAgent local RPC executor
        agent_db = Database(tmp_path / "agent_rpc.db")
        await agent_db.connect()
        try:
            nut_client = NutTcpClient(host="127.0.0.1", port=3494)
            nut_client.list_commands = AsyncMock(  # type: ignore[method-assign]
                return_value=[{"name": "beeper.mute", "description": "Mute", "risk": "safe", "category": "beeper"}]
            )
            nut_client.list_writable_vars = AsyncMock(return_value=[])  # type: ignore[method-assign]
            nut_client.run_instant_command = AsyncMock(return_value=(True, "OK"))  # type: ignore[method-assign]
            nut_client.set_var = AsyncMock(return_value=(True, "OK"))  # type: ignore[method-assign]
            nut_manager = MagicMock()
            nut_manager.read_config.return_value = (
                {"users": [{"username": "admin", "password": "secret", "instcmds": ["ALL"], "actions": ["SET"]}]},
                "",
            )
            agent = RepeaterAgent(
                db=agent_db,
                nut_client=nut_client,
                nut_manager=nut_manager,
                secret_box=SecretBox(tmp_path),
                event_log=EventLogService(agent_db),
            )

            # Bridge hub session WS send_json -> agent._execute_local_rpc -> hub session pending_rpcs
            incoming_queue: asyncio.Queue[dict] = asyncio.Queue()
            from fastapi import WebSocketDisconnect

            class FakeWS:
                headers = {"authorization": f"Bearer {joined.auth_token}"}
                query_params: dict[str, str] = {}
                client = type("C", (), {"host": "10.0.0.30"})()

                async def accept(self) -> None:
                    return None

                async def close(self, code: int = 1000) -> None:
                    await incoming_queue.put({"_close": code})

                async def send_json(self, data: dict) -> None:
                    if data.get("type") == "rpc_request":
                        res = await agent._execute_local_rpc(data["action"], data.get("params") or {})
                        await incoming_queue.put(
                            {
                                "type": "rpc_response",
                                "id": data["id"],
                                "ok": True,
                                "result": res,
                            }
                        )

                async def receive_json(self) -> dict:
                    msg = await incoming_queue.get()
                    if "_close" in msg:
                        raise WebSocketDisconnect(code=msg["_close"])
                    return msg

            ws_task = asyncio.create_task(hub.handle_websocket(FakeWS()))  # type: ignore[arg-type]
            await asyncio.sleep(0.05)

            caps = await hub.call_rpc(joined.node_id, "get_capabilities", {"ups_name": "apc"})
            assert caps["credentials_configured"] is True
            assert caps["commands"][0]["name"] == "beeper.mute"

            cmd_res = await hub.call_rpc(
                joined.node_id, "run_command", {"ups_name": "apc", "command": "beeper.mute"}
            )
            assert cmd_res["ok"] is True

            var_res = await hub.call_rpc(
                joined.node_id, "set_var", {"ups_name": "apc", "var_name": "battery.charge.low", "value": "20"}
            )
            assert var_res["ok"] is True

            await incoming_queue.put({"_close": 1000})
            await ws_task
        finally:
            await agent_db.close()
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_repeater_agent_role_endpoints_and_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "webnut_role", "repeater")
    db = Database(tmp_path / "agent_test.db")
    await db.connect()
    try:
        ws_hub = WebSocketHub()
        event_log = EventLogService(db, ws_hub=ws_hub)
        secret_box = SecretBox(tmp_path)
        nut_client = NutTcpClient(host="127.0.0.1", port=3494)
        nut_client.fetch_telemetry = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                model_dump=lambda: {
                    "connected": True,
                    "error": None,
                    "ups_list": [
                        {
                            "name": "ups1",
                            "description": "Local Basement UPS",
                            "status": "OL",
                            "status_tokens": ["OL"],
                            "battery_charge": 100.0,
                            "ups_load": 18.0,
                            "raw_vars": {"ups.status": "OL"},
                        }
                    ],
                    "updated_at": utc_now_iso(),
                }
            )
        )
        agent = RepeaterAgent(
            db=db,
            nut_client=nut_client,
            nut_manager=MagicMock(),
            secret_box=secret_box,
            event_log=event_log,
            ws_hub=ws_hub,
            app_version="2.3.0",
        )
        await agent.poll_local_once()

        # Simulate paired state in DB to verify main_name namespacing preview
        await db.execute(
            """
            UPDATE repeater_link_config
            SET paired = 1, main_url = 'http://192.168.1.10:8765',
                node_id = 1, node_name = 'Basement', node_slug = 'basement',
                updated_at = ?
            WHERE id = 1
            """,
            (utc_now_iso(),),
        )

        app = FastAPI()
        app.include_router(system_router)
        app.include_router(repeaters_router)
        app.include_router(nut_router)
        app.state.repeater_agent = agent
        app.state.db = db

        with TestClient(app) as client:
            info = client.get("/api/system/info").json()
            assert info["role"] == "repeater"
            assert info["paired"] is True
            assert info["node_name"] == "Basement"

            link = client.get("/api/repeaters/link").json()
            assert link["paired"] is True
            assert link["ups_preview"][0]["local_name"] == "ups1"
            assert link["ups_preview"][0]["main_name"] == "basement_ups1"

            tel = client.get("/api/nut/telemetry").json()
            assert tel["connected"] is True
            assert tel["ups_list"][0]["name"] == "ups1"
    finally:
        await db.close()
