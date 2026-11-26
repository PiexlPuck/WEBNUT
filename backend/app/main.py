"""FastAPI application entrypoint, lifespan management, WebSocket hub, and static UI serving."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api.control import router as control_router
from app.api.drills import router as drills_router
from app.api.events import router as events_router
from app.api.hosts import router as hosts_router
from app.api.integrations import get_prometheus_metrics, router as integrations_router
from app.api.notifications import router as notifications_router
from app.api.nut import router as nut_router
from app.api.orchestrator import router as orchestrator_router
from app.api.repeaters import router as repeaters_router
from app.api.system import router as system_router
from app.config import settings
from app.database import Database, fetch_public_hosts
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.nut.manager import NutConfigManager
from app.nut.parser import parse_nut_conf, parse_ups_conf, parse_upsd_conf, parse_upsmon_conf
from app.nut.repeater import NutRepeaterServer
from app.nut.supervisor import NutSupervisor
from app.services.crypto import SecretBox
from app.services.drill_scheduler import FlowDrillScheduler
from app.services.event_log import EventLogService
from app.services.repeater_agent import RepeaterAgent
from app.services.repeater_hub import RepeaterHub
from app.services.selftest_scheduler import SelfTestScheduler
from app.services.version_checker import VersionChecker, read_local_version
from app.services.ws_hub import WebSocketHub
from app.wol.monitor import HostMonitor
from app.wol.orchestrator import RecoveryOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _bootstrap_nut_daemons(nut_dir: Path, supervisor: NutSupervisor) -> None:
    """Start upsd/upsmon on boot if valid config entries exist."""
    nut_conf_path = nut_dir / "nut.conf"
    ups_conf_path = nut_dir / "ups.conf"
    upsmon_conf_path = nut_dir / "upsmon.conf"

    if not nut_conf_path.exists():
        return

    nut_conf = parse_nut_conf(nut_conf_path.read_text(encoding="utf-8", errors="replace"))
    ups_conf = (
        parse_ups_conf(ups_conf_path.read_text(encoding="utf-8", errors="replace"))
        if ups_conf_path.exists()
        else None
    )
    upsmon_conf = (
        parse_upsmon_conf(upsmon_conf_path.read_text(encoding="utf-8", errors="replace"))
        if upsmon_conf_path.exists()
        else None
    )

    has_ups = bool(ups_conf and len(ups_conf.ups_list) > 0)
    has_monitors = bool(upsmon_conf and len(upsmon_conf.monitors) > 0)

    ok, detail = await supervisor.sync_services(nut_conf.mode, has_ups, has_monitors)
    if not ok:
        logger.warning("Initial NUT daemon sync note: %s", detail)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(settings.db_path)
    await db.connect()

    role = settings.webnut_role
    ws_hub = WebSocketHub()
    event_log = EventLogService(db, ws_hub=ws_hub)
    secret_box = SecretBox(settings.webnut_data_dir)
    supervisor = NutSupervisor(settings.webnut_supervisor_conf, nut_dir=settings.webnut_nut_dir)
    nut_manager = NutConfigManager(settings.webnut_nut_dir, supervisor, event_log)

    conn_row = await db.fetch_one("SELECT * FROM nut_connection_config WHERE id = 1")
    is_external = (role == "main") and bool(conn_row and conn_row["mode"] == "external")
    if is_external and conn_row:
        nut_host = str(conn_row["host"])
        nut_port = int(conn_row["port"])
    else:
        nut_host = settings.nut_host
        nut_port = settings.webnut_internal_nut_port
        upsd_p = settings.webnut_nut_dir / "upsd.conf"
        if upsd_p.exists():
            upsd_cfg = parse_upsd_conf(upsd_p.read_text(encoding="utf-8", errors="replace"))
            if upsd_cfg.listens:
                nut_port = int(upsd_cfg.listens[0].port)

    nut_client = NutTcpClient(host=nut_host, port=nut_port)
    dispatcher = NotificationDispatcher(db, event_log)
    version_checker = VersionChecker(
        db,
        dispatcher,
        event_log,
        ws_hub=ws_hub,
        github_repo=settings.webnut_github_repo,
        check_interval_sec=settings.webnut_update_check_interval_sec,
        version_file=settings.webnut_version_file,
    )

    app.state.role = role
    app.state.db = db
    app.state.ws_hub = ws_hub
    app.state.event_log = event_log
    app.state.secret_box = secret_box
    app.state.supervisor = supervisor
    app.state.nut_manager = nut_manager
    app.state.nut_client = nut_client
    app.state.dispatcher = dispatcher
    app.state.version_checker = version_checker
    app.state.repeater = None
    app.state.repeater_hub = None
    app.state.repeater_agent = None
    app.state.host_monitor = None
    app.state.orchestrator = None
    app.state.selftest_scheduler = None
    app.state.drill_scheduler = None

    if not is_external:
        try:
            await _bootstrap_nut_daemons(settings.webnut_nut_dir, supervisor)
        except Exception as exc:
            logger.exception("Initial NUT daemon bootstrap failed (continuing UI startup): %s", exc)

    if role == "repeater":
        repeater_agent = RepeaterAgent(
            db=db,
            nut_client=nut_client,
            nut_manager=nut_manager,
            secret_box=secret_box,
            event_log=event_log,
            ws_hub=ws_hub,
            app_version=read_local_version(settings.webnut_version_file),
        )
        app.state.repeater_agent = repeater_agent
        await repeater_agent.start()
        version_checker.start()

        await event_log.record(
            "system",
            "info",
            "app_started",
            "WebNUT Repeater Node started",
            {"role": "repeater", "web_port": settings.web_port, "internal_nut_port": nut_port},
        )

        try:
            yield
        finally:
            await version_checker.stop()
            await repeater_agent.stop()
            await db.close()
        return

    # Role: main
    repeater = NutRepeaterServer(bind_host="0.0.0.0", port=settings.nut_port)
    repeater_hub = RepeaterHub(
        db=db,
        event_log=event_log,
        dispatcher=dispatcher,
        ws_hub=ws_hub,
        heartbeat_timeout_sec=settings.webnut_repeater_heartbeat_timeout_sec,
    )
    orchestrator = RecoveryOrchestrator(
        db,
        nut_client,
        dispatcher,
        event_log,
        ws_hub=ws_hub,
        repeater=repeater,
        secret_box=secret_box,
        repeater_hub=repeater_hub,
    )
    host_monitor = HostMonitor(
        db,
        ws_hub=ws_hub,
        boot_grace_check=orchestrator.is_cold_start_grace_active,
    )
    repeater_hub.set_on_telemetry_change(orchestrator.refresh_from_repeaters)
    selftest_scheduler = SelfTestScheduler(
        db,
        nut_client,
        nut_manager,
        orchestrator,
        dispatcher,
        event_log,
        ws_hub=ws_hub,
        repeater_hub=repeater_hub,
    )
    drill_scheduler = FlowDrillScheduler(
        db,
        orchestrator,
        dispatcher,
        event_log,
        ws_hub=ws_hub,
    )

    app.state.repeater = repeater
    app.state.repeater_hub = repeater_hub
    app.state.host_monitor = host_monitor
    app.state.orchestrator = orchestrator
    app.state.selftest_scheduler = selftest_scheduler
    app.state.drill_scheduler = drill_scheduler

    await repeater_hub.start()
    await orchestrator.reconcile_on_startup()
    orch_cfg = await orchestrator.get_config()
    if orch_cfg.repeater_enabled:
        await repeater.start(orch_cfg.repeater_port)
    host_monitor.start()
    orchestrator.start()
    version_checker.start()
    selftest_scheduler.start()
    drill_scheduler.start()

    await event_log.record(
        "system",
        "info",
        "app_started",
        "WebNUT server started",
        {"role": "main", "web_port": settings.web_port, "nut_port": settings.nut_port, "repeater_port": repeater.port},
    )

    try:
        yield
    finally:
        await drill_scheduler.stop()
        await selftest_scheduler.stop()
        await version_checker.stop()
        await orchestrator.stop()
        await host_monitor.stop()
        await repeater.stop()
        await repeater_hub.stop()
        await db.close()


app = FastAPI(
    title="WEBNUT — Unified NUT & WOLNUT Manager",
    version=read_local_version(settings.webnut_version_file),
    lifespan=lifespan,
)

app.include_router(nut_router)
app.include_router(hosts_router)
app.include_router(orchestrator_router)
app.include_router(notifications_router)
app.include_router(events_router)
app.include_router(system_router)
app.include_router(integrations_router)
app.include_router(control_router)
app.include_router(drills_router)
app.include_router(repeaters_router)


@app.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics_root(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> PlainTextResponse:
    return await get_prometheus_metrics(request, x_api_key=x_api_key, authorization=authorization)


@app.websocket("/ws/repeater")
async def websocket_repeater_tunnel(websocket: WebSocket) -> None:
    repeater_hub = getattr(websocket.app.state, "repeater_hub", None)
    if repeater_hub is None:
        await websocket.close(code=4000)
        return
    await repeater_hub.handle_websocket(websocket)


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    ws_hub: WebSocketHub = websocket.app.state.ws_hub
    await ws_hub.connect(websocket)
    try:
        # Send initial full snapshot on connect
        role = getattr(websocket.app.state, "role", settings.webnut_role)
        db = websocket.app.state.db
        supervisor = websocket.app.state.supervisor
        version_checker = websocket.app.state.version_checker

        if role == "repeater":
            agent = websocket.app.state.repeater_agent
            await websocket.send_json({"type": "telemetry", "data": agent.latest_telemetry})
            link_status = await agent.get_link_status()
            await websocket.send_json({"type": "repeater_link", "data": link_status.model_dump()})
            services = await supervisor.get_services_status(external_mode=False)
            await websocket.send_json({"type": "services", "data": [s.model_dump() for s in services]})
            ver_status = await version_checker.get_status()
            await websocket.send_json({"type": "update_status", "data": ver_status.model_dump()})
        else:
            orchestrator = websocket.app.state.orchestrator
            repeater_hub = getattr(websocket.app.state, "repeater_hub", None)

            await websocket.send_json({"type": "telemetry", "data": orchestrator.latest_telemetry})
            orch_state = await orchestrator.get_state()
            await websocket.send_json({"type": "orchestrator", "data": orch_state.model_dump()})
            hosts = await fetch_public_hosts(db)
            await websocket.send_json({"type": "hosts", "data": hosts})
            conn_row = await db.fetch_one("SELECT mode FROM nut_connection_config WHERE id = 1")
            services = await supervisor.get_services_status(
                external_mode=bool(conn_row and conn_row["mode"] == "external")
            )
            await websocket.send_json({"type": "services", "data": [s.model_dump() for s in services]})
            if repeater_hub is not None:
                nodes = await repeater_hub.list_nodes()
                await websocket.send_json(
                    {"type": "repeaters", "data": [n.model_dump() for n in nodes]}
                )
            ver_status = await version_checker.get_status()
            await websocket.send_json({"type": "update_status", "data": ver_status.model_dump()})

        while True:
            # Keep connection open; ignore client keepalive pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_hub.disconnect(websocket)
    except Exception:
        await ws_hub.disconnect(websocket)


# Serve compiled frontend static assets if present
_static_dir = settings.webnut_static_dir
if _static_dir.exists() and (_static_dir / "index.html").exists():
    assets_dir = _static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_static_dir / full_path).resolve()
        try:
            candidate.relative_to(_static_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static_dir / "index.html")
