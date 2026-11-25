"""NUT telemetry, USB discovery, config management, and daemon service endpoints."""

from __future__ import annotations

from typing import Literal
import secrets
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.database import utc_now_iso
from app.models.schemas import (
    SECRET_MASK,
    ConnectedNutClient,
    CreateSecondaryUserRequest,
    CreateSecondaryUserResponse,
    HardwareScanResponse,
    NutClientConfigRequest,
    NutClientConfigResponse,
    NutClientsOverview,
    NutConfigFile,
    NutConfigPreviewResponse,
    NutConfigReadResponse,
    NutConfigSaveRequest,
    NutConfigSaveResponse,
    NutConnectionConfigRequest,
    NutConnectionConfigView,
    NutConnectionTestRequest,
    NutConnectionTestResponse,
    NutServiceStatus,
    ScannedUsbDevice,
    TelemetrySnapshot,
)
from app.nut.client import NutTcpClient
from app.nut.client_config import detect_local_lan_ip, generate_client_config
from app.nut.manager import NutConfigApplyError
from app.nut.parser import parse_nut_conf, parse_ups_conf, parse_upsmon_conf
from app.nut.scanner import scan_all_hardware, scan_usb_ups

router = APIRouter(prefix="/api/nut", tags=["nut"])


async def _get_connection_row(db) -> dict:
    row = await db.fetch_one("SELECT * FROM nut_connection_config WHERE id = 1")
    if row is None:
        return {
            "mode": "local",
            "host": settings.nut_host,
            "port": settings.nut_port,
            "username": "",
            "password": "",
        }
    return row


@router.get("/telemetry", response_model=TelemetrySnapshot)
async def get_telemetry(request: Request) -> TelemetrySnapshot:
    orchestrator = request.app.state.orchestrator
    return TelemetrySnapshot.model_validate(orchestrator.latest_telemetry)


@router.post("/scan", response_model=list[ScannedUsbDevice])
async def scan_ups(request: Request) -> list[ScannedUsbDevice]:
    event_log = request.app.state.event_log
    try:
        devices = await scan_usb_ups()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await event_log.record(
        "nut_config",
        "info",
        "nut_usb_scan",
        f"USB UPS scan discovered {len(devices)} device(s)",
        {"count": len(devices)},
    )
    return devices


@router.post("/scan-hardware", response_model=HardwareScanResponse)
async def scan_hardware(request: Request) -> HardwareScanResponse:
    event_log = request.app.state.event_log
    result = await scan_all_hardware()
    await event_log.record(
        "nut_config",
        "info",
        "nut_hardware_scan",
        (
            f"Hardware scan found {len(result.nut_usb_devices)} NUT USB, "
            f"{len(result.serial_devices)} serial, and {len(result.raw_usb_devices)} raw USB device(s)"
        ),
        {
            "nut_usb_count": len(result.nut_usb_devices),
            "serial_count": len(result.serial_devices),
            "raw_usb_count": len(result.raw_usb_devices),
        },
    )
    return result


@router.get("/config/{filename}", response_model=NutConfigReadResponse)
async def get_nut_config(filename: NutConfigFile, request: Request) -> NutConfigReadResponse:
    manager = request.app.state.nut_manager
    parsed, raw = manager.read_config(filename, mask_secrets=True)
    return NutConfigReadResponse(filename=filename, parsed=parsed, raw=raw)


@router.post("/config/{filename}/preview", response_model=NutConfigPreviewResponse)
async def preview_nut_config(
    filename: NutConfigFile,
    payload: NutConfigSaveRequest,
    request: Request,
) -> NutConfigPreviewResponse:
    manager = request.app.state.nut_manager
    try:
        raw = manager.preview_config(filename, payload.parsed)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NutConfigPreviewResponse(filename=filename, raw=raw)


@router.put("/config/{filename}", response_model=NutConfigSaveResponse)
async def save_nut_config(
    filename: NutConfigFile,
    payload: NutConfigSaveRequest,
    request: Request,
) -> NutConfigSaveResponse:
    manager = request.app.state.nut_manager
    try:
        raw, applied, message = await manager.save_config(
            filename,
            payload.parsed,
            apply_reload=payload.apply_reload,
        )
        # Push updated service status after config change
        conn_row = await _get_connection_row(request.app.state.db)
        services = await request.app.state.supervisor.get_services_status(
            external_mode=(conn_row["mode"] == "external")
        )
        await request.app.state.ws_hub.broadcast(
            "services", [s.model_dump() for s in services]
        )
        return NutConfigSaveResponse(
            filename=filename,
            raw=raw,
            applied=applied,
            message=message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NutConfigApplyError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": exc.message,
                "stderr": exc.stderr,
                "rolled_back": exc.rolled_back,
            },
        ) from exc


@router.get("/services", response_model=list[NutServiceStatus])
async def list_nut_services(request: Request) -> list[NutServiceStatus]:
    conn_row = await _get_connection_row(request.app.state.db)
    return await request.app.state.supervisor.get_services_status(
        external_mode=(conn_row["mode"] == "external")
    )


@router.get("/connection", response_model=NutConnectionConfigView)
async def get_nut_connection(request: Request) -> NutConnectionConfigView:
    row = await _get_connection_row(request.app.state.db)
    pw = str(row.get("password") or "")
    return NutConnectionConfigView(
        mode=row["mode"],
        host=row["host"],
        port=int(row["port"]),
        username=str(row.get("username") or ""),
        password=SECRET_MASK if pw else "",
        password_configured=bool(pw),
    )


@router.post("/connection/test", response_model=NutConnectionTestResponse)
async def test_nut_connection(payload: NutConnectionTestRequest) -> NutConnectionTestResponse:
    probe = NutTcpClient(host=payload.host.strip(), port=payload.port, timeout=4.0)
    snap = await probe.fetch_telemetry()
    if not snap.connected:
        return NutConnectionTestResponse(
            ok=False,
            message=snap.error or f"Failed to connect to {payload.host}:{payload.port}",
            ups_list=[],
        )
    if not snap.ups_list:
        return NutConnectionTestResponse(
            ok=True,
            message=f"Connected to {payload.host}:{payload.port}, but upsd returned 0 UPS units.",
            ups_list=[],
        )
    names = ", ".join(u.name for u in snap.ups_list)
    return NutConnectionTestResponse(
        ok=True,
        message=f"Connected to {payload.host}:{payload.port} — found {len(snap.ups_list)} UPS unit(s): {names}",
        ups_list=snap.ups_list,
    )


@router.put("/connection", response_model=NutConnectionConfigView)
async def update_nut_connection(
    payload: NutConnectionConfigRequest,
    request: Request,
) -> NutConnectionConfigView:
    db = request.app.state.db
    existing = await _get_connection_row(db)
    password = (
        str(existing.get("password") or "")
        if payload.password == SECRET_MASK
        else payload.password
    )
    target_host = settings.nut_host if payload.mode == "local" else payload.host.strip()
    target_port = settings.nut_port if payload.mode == "local" else int(payload.port)
    now = utc_now_iso()

    await db.execute(
        """
        INSERT INTO nut_connection_config (id, mode, host, port, username, password, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            mode = excluded.mode,
            host = excluded.host,
            port = excluded.port,
            username = excluded.username,
            password = excluded.password,
            updated_at = excluded.updated_at
        """,
        (
            payload.mode,
            target_host,
            target_port,
            payload.username.strip(),
            password,
            now,
        ),
    )

    nut_client: NutTcpClient = request.app.state.nut_client
    nut_client.configure(target_host, target_port)

    supervisor = request.app.state.supervisor
    if payload.mode == "external":
        await supervisor.stop_service("upsd")
        await supervisor.stop_service("drivers")
    else:
        nut_dir = settings.webnut_nut_dir
        nut_conf_p = nut_dir / "nut.conf"
        ups_conf_p = nut_dir / "ups.conf"
        upsmon_conf_p = nut_dir / "upsmon.conf"
        nut_mode = (
            parse_nut_conf(nut_conf_p.read_text(encoding="utf-8", errors="replace")).mode
            if nut_conf_p.exists()
            else "netserver"
        )
        has_ups = bool(
            ups_conf_p.exists()
            and len(parse_ups_conf(ups_conf_p.read_text(encoding="utf-8", errors="replace")).ups_list)
            > 0
        )
        has_mon = bool(
            upsmon_conf_p.exists()
            and len(
                parse_upsmon_conf(
                    upsmon_conf_p.read_text(encoding="utf-8", errors="replace")
                ).monitors
            )
            > 0
        )
        await supervisor.sync_services(nut_mode, has_ups, has_mon)

    # Refresh telemetry and broadcast updated state immediately
    snapshot = await nut_client.fetch_telemetry()
    await request.app.state.orchestrator.evaluate_telemetry(snapshot.model_dump())

    services = await supervisor.get_services_status(external_mode=(payload.mode == "external"))
    await request.app.state.ws_hub.broadcast("services", [s.model_dump() for s in services])

    await request.app.state.event_log.record(
        "nut_config",
        "info",
        "nut_connection_updated",
        (
            f"Switched NUT source to external server {target_host}:{target_port}"
            if payload.mode == "external"
            else f"Switched NUT source to local built-in server ({target_host}:{target_port})"
        ),
        {"mode": payload.mode, "host": target_host, "port": target_port},
    )

    return await get_nut_connection(request)


@router.post("/services/{service}/restart")
async def restart_nut_service(
    service: Literal["upsd", "upsmon", "drivers"],
    request: Request,
) -> dict:
    supervisor = request.app.state.supervisor
    event_log = request.app.state.event_log
    ok, detail = await supervisor.restart_service(service)
    await event_log.record(
        "nut_service",
        "success" if ok else "error",
        "nut_service_restart",
        f"Restart {service}: {'succeeded' if ok else 'failed'}",
        {"service": service, "detail": detail},
    )
    conn_row = await _get_connection_row(request.app.state.db)
    services = await supervisor.get_services_status(
        external_mode=(conn_row["mode"] == "external")
    )
    await request.app.state.ws_hub.broadcast("services", [s.model_dump() for s in services])
    if not ok:
        raise HTTPException(status_code=500, detail=detail)
    return {"service": service, "ok": True, "detail": detail}


@router.get("/clients", response_model=NutClientsOverview)
async def get_nut_clients(request: Request) -> NutClientsOverview:
    manager = request.app.state.nut_manager
    orchestrator = request.app.state.orchestrator
    db = request.app.state.db

    ups_parsed, _ = manager.read_config("ups.conf", mask_secrets=True)
    users_parsed, _ = manager.read_config("upsd.users", mask_secrets=True)

    ups_names_set: list[str] = [
        u["name"] for u in ups_parsed.get("ups_list", []) if u.get("name")
    ]
    telemetry = orchestrator.latest_telemetry
    for u in telemetry.get("ups_list", []):
        uname = u.get("name")
        if uname and uname not in ups_names_set:
            ups_names_set.append(uname)

    available_users = [
        u["username"] for u in users_parsed.get("users", []) if u.get("username")
    ]

    host_rows = await db.fetch_all("SELECT id, name, ip_or_host FROM hosts")
    ip_to_host = {
        str(r["ip_or_host"]).strip(): (int(r["id"]), str(r["name"])) for r in host_rows
    }

    connected_clients: list[ConnectedNutClient] = []
    for u in telemetry.get("ups_list", []):
        uname = str(u.get("name", ""))
        for cip in u.get("connected_clients", []) or []:
            matched = ip_to_host.get(cip)
            connected_clients.append(
                ConnectedNutClient(
                    ups_name=uname,
                    client_ip=cip,
                    matched_host_id=matched[0] if matched else None,
                    matched_host_name=matched[1] if matched else None,
                )
            )

    conn_row = await _get_connection_row(db)
    default_ip = (
        str(conn_row["host"])
        if conn_row["mode"] == "external"
        else detect_local_lan_ip()
    )
    default_port = (
        int(conn_row["port"])
        if conn_row["mode"] == "external"
        else settings.nut_port
    )

    return NutClientsOverview(
        server_ip=default_ip,
        nut_port=default_port,
        ups_names=ups_names_set,
        available_users=available_users,
        connected_clients=connected_clients,
    )


@router.post("/clients/create-user", response_model=CreateSecondaryUserResponse)
async def create_secondary_nut_user(
    payload: CreateSecondaryUserRequest,
    request: Request,
) -> CreateSecondaryUserResponse:
    manager = request.app.state.nut_manager
    username = payload.username.strip()
    password = payload.password.strip() or secrets.token_urlsafe(16)

    parsed_users, _ = manager.read_config("upsd.users", mask_secrets=True)
    users_list = list(parsed_users.get("users", []))
    updated = False
    for entry in users_list:
        if entry.get("username") == username:
            entry["password"] = password
            entry["upsmon_role"] = "secondary"
            updated = True
            break
    if not updated:
        users_list.append(
            {
                "username": username,
                "password": password,
                "actions": [],
                "instcmds": [],
                "upsmon_role": "secondary",
            }
        )
    parsed_users["users"] = users_list

    try:
        _, applied, message = await manager.save_config(
            "upsd.users",
            parsed_users,
            apply_reload=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NutConfigApplyError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": exc.message,
                "stderr": exc.stderr,
                "rolled_back": exc.rolled_back,
            },
        ) from exc

    return CreateSecondaryUserResponse(
        username=username,
        password=password,
        upsmon_role="secondary",
        applied=applied,
        message=message,
    )


@router.post("/clients/generate", response_model=NutClientConfigResponse)
async def generate_nut_client_config(payload: NutClientConfigRequest) -> NutClientConfigResponse:
    try:
        return generate_client_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
