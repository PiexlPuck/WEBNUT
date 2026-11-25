"""FastAPI application entrypoint, lifespan management, WebSocket hub, and static UI serving."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.events import router as events_router
from app.api.hosts import router as hosts_router
from app.api.notifications import router as notifications_router
from app.api.nut import router as nut_router
from app.api.orchestrator import router as orchestrator_router
from app.api.system import router as system_router
from app.config import settings
from app.database import Database
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.nut.manager import NutConfigManager
from app.nut.parser import parse_nut_conf, parse_ups_conf, parse_upsmon_conf
from app.nut.supervisor import NutSupervisor
from app.services.event_log import EventLogService
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

    ws_hub = WebSocketHub()
    event_log = EventLogService(db, ws_hub=ws_hub)
    supervisor = NutSupervisor(settings.webnut_supervisor_conf)
    nut_manager = NutConfigManager(settings.webnut_nut_dir, supervisor, event_log)
    nut_client = NutTcpClient(host=settings.nut_host, port=settings.nut_port)
    dispatcher = NotificationDispatcher(db, event_log)
    host_monitor = HostMonitor(db, ws_hub=ws_hub)
    orchestrator = RecoveryOrchestrator(db, nut_client, dispatcher, event_log, ws_hub=ws_hub)
    version_checker = VersionChecker(
        db,
        dispatcher,
        event_log,
        ws_hub=ws_hub,
        github_repo=settings.webnut_github_repo,
        check_interval_sec=settings.webnut_update_check_interval_sec,
        version_file=settings.webnut_version_file,
    )

    app.state.db = db
    app.state.ws_hub = ws_hub
    app.state.event_log = event_log
    app.state.supervisor = supervisor
    app.state.nut_manager = nut_manager
    app.state.nut_client = nut_client
    app.state.dispatcher = dispatcher
    app.state.host_monitor = host_monitor
    app.state.orchestrator = orchestrator
    app.state.version_checker = version_checker

    await _bootstrap_nut_daemons(settings.webnut_nut_dir, supervisor)
    await orchestrator.reconcile_on_startup()
    host_monitor.start()
    orchestrator.start()
    version_checker.start()

    await event_log.record(
        "system",
        "info",
        "app_started",
        "WEBNUT server started",
        {"web_port": settings.web_port, "nut_port": settings.nut_port},
    )

    try:
        yield
    finally:
        await version_checker.stop()
        await orchestrator.stop()
        await host_monitor.stop()
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


@app.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok"}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    ws_hub: WebSocketHub = websocket.app.state.ws_hub
    await ws_hub.connect(websocket)
    try:
        # Send initial full snapshot on connect
        orchestrator = websocket.app.state.orchestrator
        db = websocket.app.state.db
        supervisor = websocket.app.state.supervisor

        await websocket.send_json({"type": "telemetry", "data": orchestrator.latest_telemetry})
        orch_state = await orchestrator.get_state()
        await websocket.send_json({"type": "orchestrator", "data": orch_state.model_dump()})
        hosts = await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
        await websocket.send_json({"type": "hosts", "data": hosts})
        services = await supervisor.get_services_status()
        await websocket.send_json({"type": "services", "data": [s.model_dump() for s in services]})
        version_checker = websocket.app.state.version_checker
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
