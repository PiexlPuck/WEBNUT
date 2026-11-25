"""Repeater Node Agent: local NUT polling, pairing with Main WebNUT, and outbound WS tunnel."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

from app.config import settings
from app.database import Database, utc_now_iso
from app.models.schemas import RepeaterLinkStatusResponse, RepeaterLocalUpsPreview
from app.nut.client import NutTcpClient
from app.nut.client_config import detect_local_lan_ip
from app.services.crypto import SecretBox
from app.services.event_log import EventLogService
from app.services.repeater_hub import namespace_ups_name
from app.services.selftest_scheduler import (
    resolve_control_credentials,
    resolve_control_credentials_with_source,
)
from app.services.ws_hub import WebSocketHub

logger = logging.getLogger(__name__)


def _normalize_http_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


def _to_ws_url(http_url: str, path: str = "/ws/repeater") -> str:
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc
    base_path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{base_path}{path}"


def _ws_headers_kwarg(headers: dict[str, str]) -> dict[str, Any]:
    sig = inspect.signature(websockets.connect)
    if "additional_headers" in sig.parameters:
        return {"additional_headers": headers}
    return {"extra_headers": headers}


class RepeaterAgent:
    """Runs on WEBNUT_ROLE=repeater to poll local NUT and stream to Main WebNUT."""

    def __init__(
        self,
        db: Database,
        nut_client: NutTcpClient,
        nut_manager: Any,
        secret_box: SecretBox,
        event_log: EventLogService,
        ws_hub: WebSocketHub | None = None,
        app_version: str = "2.3.0",
        poll_interval_sec: float = 3.0,
    ) -> None:
        self.db = db
        self.nut_client = nut_client
        self.nut_manager = nut_manager
        self.secret_box = secret_box
        self.event_log = event_log
        self.ws_hub = ws_hub
        self.app_version = app_version
        self.poll_interval_sec = poll_interval_sec

        self._latest_telemetry: dict[str, Any] = {
            "connected": False,
            "error": "Initializing",
            "ups_list": [],
            "updated_at": utc_now_iso(),
        }
        self._connected = False
        self._last_push_at: str | None = None
        self._last_error: str | None = None
        self._latency_ms: float | None = None

        self._stop_event = asyncio.Event()
        self._push_event = asyncio.Event()
        self._reconnect_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._tunnel_task: asyncio.Task[None] | None = None

    @property
    def latest_telemetry(self) -> dict[str, Any]:
        return self._latest_telemetry

    async def _get_link_row(self) -> dict[str, Any]:
        row = await self.db.fetch_one("SELECT * FROM repeater_link_config WHERE id = 1")
        if row is None:
            return {
                "paired": 0,
                "main_url": "",
                "node_id": None,
                "node_name": "",
                "node_slug": "",
                "auth_token_enc": "",
                "public_url": "",
                "push_interval_sec": 3,
                "updated_at": utc_now_iso(),
            }
        return row

    async def get_link_status(self) -> RepeaterLinkStatusResponse:
        row = await self._get_link_row()
        paired = bool(row.get("paired"))
        slug = str(row.get("node_slug") or "")
        previews: list[RepeaterLocalUpsPreview] = []
        for u in self._latest_telemetry.get("ups_list") or []:
            local_name = str(u.get("name") or "")
            if not local_name:
                continue
            main_name = namespace_ups_name(slug, local_name) if (paired and slug) else local_name
            previews.append(
                RepeaterLocalUpsPreview(
                    local_name=local_name,
                    main_name=main_name,
                    status=str(u.get("status") or "UNKNOWN"),
                    battery_charge=u.get("battery_charge"),
                    ups_load=u.get("ups_load"),
                    description=str(u.get("description") or ""),
                )
            )
        return RepeaterLinkStatusResponse(
            paired=paired,
            connected=self._connected if paired else False,
            main_url=str(row.get("main_url") or ""),
            node_id=int(row["node_id"]) if row.get("node_id") is not None else None,
            node_name=str(row.get("node_name") or ""),
            node_slug=slug,
            public_url=str(row.get("public_url") or ""),
            push_interval_sec=int(row.get("push_interval_sec") or 3),
            last_push_at=self._last_push_at,
            last_error=self._last_error,
            latency_ms=self._latency_ms,
            ups_preview=previews,
            updated_at=row.get("updated_at"),
        )

    async def _broadcast_link_status(self) -> None:
        if self.ws_hub is not None:
            status = await self.get_link_status()
            await self.ws_hub.broadcast("repeater_link", status.model_dump())

    async def pair(
        self,
        main_url: str,
        join_token: str,
        node_name: str,
        public_url: str = "",
    ) -> RepeaterLinkStatusResponse:
        norm_url = _normalize_http_url(main_url)
        if not norm_url:
            raise ValueError("Main WebNUT URL is required")
        ui_url = (
            _normalize_http_url(public_url)
            if public_url.strip()
            else f"http://{detect_local_lan_ip()}:{settings.web_port}"
        )
        payload = {
            "join_token": join_token.strip(),
            "node_name": node_name.strip(),
            "ui_url": ui_url,
            "app_version": self.app_version,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{norm_url}/api/repeaters/join", json=payload)
            if resp.status_code >= 400:
                detail = resp.text
                try:
                    body = resp.json()
                    detail = str(body.get("detail") or detail)
                except Exception:
                    pass
                raise RuntimeError(f"Pairing rejected by Main WebNUT ({resp.status_code}): {detail}")
            data = resp.json()

        auth_token = str(data.get("auth_token") or "")
        if not auth_token:
            raise RuntimeError("Main WebNUT did not return an auth token")

        enc = self.secret_box.encrypt(auth_token)
        now_iso = utc_now_iso()
        await self.db.execute(
            """
            UPDATE repeater_link_config
            SET paired = 1,
                main_url = ?,
                node_id = ?,
                node_name = ?,
                node_slug = ?,
                auth_token_enc = ?,
                public_url = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                norm_url,
                int(data["node_id"]),
                str(data["node_name"]),
                str(data["node_slug"]),
                enc,
                ui_url,
                now_iso,
            ),
        )
        self._last_error = None
        await self.event_log.record(
            "repeater",
            "success",
            "repeater_paired",
            f"Paired with Main WebNUT at {norm_url} as '{data['node_name']}' ({data['node_slug']}_*)",
            {"main_url": norm_url, "node_id": data["node_id"], "slug": data["node_slug"]},
        )
        self.trigger_reconnect()
        await self._broadcast_link_status()
        return await self.get_link_status()

    async def unpair(self) -> RepeaterLinkStatusResponse:
        now_iso = utc_now_iso()
        await self.db.execute(
            """
            UPDATE repeater_link_config
            SET paired = 0,
                main_url = '',
                node_id = NULL,
                node_name = '',
                node_slug = '',
                auth_token_enc = '',
                updated_at = ?
            WHERE id = 1
            """,
            (now_iso,),
        )
        self._connected = False
        self._last_error = None
        self._latency_ms = None
        await self.event_log.record(
            "repeater",
            "info",
            "repeater_unpaired",
            "Unpaired Repeater node from Main WebNUT",
        )
        self.trigger_reconnect()
        await self._broadcast_link_status()
        return await self.get_link_status()

    def trigger_push(self) -> None:
        self._push_event.set()

    def trigger_reconnect(self) -> None:
        self._reconnect_event.set()
        self._push_event.set()

    async def poll_local_once(self) -> dict[str, Any]:
        snapshot = await self.nut_client.fetch_telemetry()
        snap_dict = snapshot.model_dump()
        self._latest_telemetry = snap_dict
        if self.ws_hub is not None:
            await self.ws_hub.broadcast("telemetry", snap_dict)
        self.trigger_push()
        await self._broadcast_link_status()
        return snap_dict

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_local_once()
            except Exception as exc:
                logger.exception("RepeaterAgent local telemetry poll failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                pass

    async def _execute_local_rpc(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "fetch_telemetry":
            snap = await self.poll_local_once()
            return {"snapshot": snap}

        ups_name = str(params.get("ups_name") or "").strip()
        if not ups_name:
            raise ValueError("Missing ups_name in RPC request")

        if action == "get_capabilities":
            raw_cmds = await self.nut_client.list_commands(ups_name)
            raw_vars = await self.nut_client.list_writable_vars(ups_name)
            username, password, source = await resolve_control_credentials_with_source(
                self.db, self.nut_manager
            )
            return {
                "commands": raw_cmds,
                "writable_vars": raw_vars,
                "credentials_configured": bool(username and password),
                "credential_source": source,
            }

        if action == "run_command":
            command = str(params.get("command") or "").strip()
            username, password = await resolve_control_credentials(self.db, self.nut_manager)
            if not username or not password:
                return {
                    "ok": False,
                    "message": (
                        "No local NUT control credentials configured on Repeater node "
                        "(check upsd.users)"
                    ),
                }
            ok, msg = await self.nut_client.run_instant_command(
                ups_name, command, username, password
            )
            await self.event_log.record(
                "ups_control",
                "success" if ok else "error",
                "ups_instant_command_remote",
                f"Main WebNUT INSTCMD '{command}' on '{ups_name}': {msg}",
                {"ups_name": ups_name, "command": command, "ok": ok},
            )
            asyncio.create_task(self.poll_local_once())
            return {"ok": ok, "message": msg}

        if action == "set_var":
            var_name = str(params.get("var_name") or "").strip()
            value = str(params.get("value") or "")
            username, password = await resolve_control_credentials(self.db, self.nut_manager)
            if not username or not password:
                return {
                    "ok": False,
                    "message": (
                        "No local NUT control credentials configured on Repeater node "
                        "(check upsd.users)"
                    ),
                }
            ok, msg = await self.nut_client.set_var(
                ups_name, var_name, value, username, password
            )
            await self.event_log.record(
                "ups_control",
                "success" if ok else "error",
                "ups_set_var_remote",
                f"Main WebNUT SET VAR '{var_name}' on '{ups_name}' = '{value}': {msg}",
                {"ups_name": ups_name, "var_name": var_name, "value": value, "ok": ok},
            )
            asyncio.create_task(self.poll_local_once())
            return {"ok": ok, "message": msg}

        raise ValueError(f"Unsupported RPC action: {action}")

    async def _tunnel_loop(self) -> None:
        backoff = 2.0
        while not self._stop_event.is_set():
            self._reconnect_event.clear()
            row = await self._get_link_row()
            paired = bool(row.get("paired"))
            main_url = str(row.get("main_url") or "")
            auth_token = self.secret_box.decrypt(str(row.get("auth_token_enc") or ""))

            if not paired or not main_url or not auth_token:
                self._connected = False
                try:
                    await asyncio.wait_for(self._reconnect_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                continue

            ws_url = _to_ws_url(main_url, "/ws/repeater")
            headers = {"Authorization": f"Bearer {auth_token}"}

            try:
                async with websockets.connect(
                    ws_url,
                    open_timeout=10,
                    close_timeout=5,
                    **_ws_headers_kwarg(headers),
                ) as ws:
                    backoff = 2.0
                    self._connected = True
                    self._last_error = None
                    ui_url = str(row.get("public_url") or "") or (
                        f"http://{detect_local_lan_ip()}:{settings.web_port}"
                    )
                    await ws.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "app_version": self.app_version,
                                "ui_url": ui_url,
                            }
                        )
                    )
                    await ws.send(
                        json.dumps({"type": "telemetry", "data": self._latest_telemetry})
                    )
                    self._last_push_at = utc_now_iso()
                    await self._broadcast_link_status()

                    heartbeat_sent_mono: dict[str, float] = {}
                    send_lock = asyncio.Lock()

                    async def _sender() -> None:
                        last_hb = time.monotonic()
                        while not self._stop_event.is_set() and not self._reconnect_event.is_set():
                            triggered = False
                            try:
                                await asyncio.wait_for(self._push_event.wait(), timeout=3.0)
                                self._push_event.clear()
                                triggered = True
                            except asyncio.TimeoutError:
                                pass

                            if self._reconnect_event.is_set() or self._stop_event.is_set():
                                break

                            if triggered:
                                async with send_lock:
                                    await ws.send(
                                        json.dumps(
                                            {"type": "telemetry", "data": self._latest_telemetry}
                                        )
                                    )
                                self._last_push_at = utc_now_iso()

                            now_mono = time.monotonic()
                            if (now_mono - last_hb) >= 10.0:
                                last_hb = now_mono
                                ts_id = f"{now_mono:.6f}"
                                heartbeat_sent_mono[ts_id] = now_mono
                                async with send_lock:
                                    await ws.send(
                                        json.dumps(
                                            {
                                                "type": "heartbeat",
                                                "ts": ts_id,
                                                "latency_ms": self._latency_ms,
                                            }
                                        )
                                    )

                    async def _receiver() -> None:
                        async for raw_msg in ws:
                            try:
                                msg = json.loads(raw_msg)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(msg, dict):
                                continue
                            mtype = str(msg.get("type") or "")
                            if mtype == "heartbeat_ack":
                                ts_id = str(msg.get("ts") or "")
                                sent_mono = heartbeat_sent_mono.pop(ts_id, None)
                                if sent_mono is not None:
                                    self._latency_ms = round(
                                        (time.monotonic() - sent_mono) * 1000.0, 1
                                    )
                                    await self._broadcast_link_status()
                            elif mtype == "rpc_request":
                                req_id = str(msg.get("id") or "")
                                action = str(msg.get("action") or "")
                                params = (
                                    msg.get("params")
                                    if isinstance(msg.get("params"), dict)
                                    else {}
                                )
                                try:
                                    res = await self._execute_local_rpc(action, params)
                                    reply = {
                                        "type": "rpc_response",
                                        "id": req_id,
                                        "ok": True,
                                        "result": res,
                                    }
                                except Exception as exc:
                                    reply = {
                                        "type": "rpc_response",
                                        "id": req_id,
                                        "ok": False,
                                        "error": str(exc),
                                    }
                                async with send_lock:
                                    await ws.send(json.dumps(reply))

                    sender_task = asyncio.create_task(_sender())
                    receiver_task = asyncio.create_task(_receiver())
                    done, pending = await asyncio.wait(
                        {sender_task, receiver_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    for task in done:
                        exc = task.exception()
                        if exc is not None:
                            raise exc
            except Exception as exc:
                self._connected = False
                self._last_error = str(exc)
                logger.warning("Repeater WS tunnel to %s failed: %s", ws_url, exc)
                await self._broadcast_link_status()
                try:
                    await asyncio.wait_for(self._reconnect_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(30.0, backoff * 1.5)

    async def start(self) -> None:
        # Optional headless auto-pairing from env vars on first boot
        row = await self._get_link_row()
        if (
            not bool(row.get("paired"))
            and settings.webnut_main_url.strip()
            and settings.webnut_join_token.strip()
        ):
            node_name = settings.webnut_node_name.strip() or socket.gethostname() or "webnut-repeater"
            try:
                await self.pair(
                    main_url=settings.webnut_main_url.strip(),
                    join_token=settings.webnut_join_token.strip(),
                    node_name=node_name,
                    public_url=settings.webnut_repeater_public_url.strip(),
                )
            except Exception as exc:
                self._last_error = f"Auto-pair failed: {exc}"
                logger.warning("Repeater auto-pair failed: %s", exc)
                await self.event_log.record(
                    "repeater",
                    "error",
                    "repeater_autopair_failed",
                    f"Headless auto-pairing with {settings.webnut_main_url.strip()} failed: {exc}",
                )

        if self._poll_task is None or self._poll_task.done():
            self._stop_event.clear()
            self._poll_task = asyncio.create_task(self._poll_loop(), name="repeater-local-poll")
        if self._tunnel_task is None or self._tunnel_task.done():
            self._tunnel_task = asyncio.create_task(self._tunnel_loop(), name="repeater-ws-tunnel")

    async def stop(self) -> None:
        self._stop_event.set()
        self._reconnect_event.set()
        self._push_event.set()
        for task in (self._poll_task, self._tunnel_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._poll_task = None
        self._tunnel_task = None
        self._connected = False
