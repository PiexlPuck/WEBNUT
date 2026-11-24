"""NUT telemetry, USB discovery, config management, and daemon service endpoints."""

from __future__ import annotations

from typing import Literal
import secrets
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.models.schemas import (
    ConnectedNutClient,
    CreateSecondaryUserRequest,
    CreateSecondaryUserResponse,
    NutClientConfigRequest,
    NutClientConfigResponse,
    NutClientsOverview,
    NutConfigFile,
    NutConfigPreviewResponse,
    NutConfigReadResponse,
    NutConfigSaveRequest,
    NutConfigSaveResponse,
    NutServiceStatus,
    ScannedUsbDevice,
    TelemetrySnapshot,
)
from app.nut.client_config import detect_local_lan_ip, generate_client_config
from app.nut.manager import NutConfigApplyError
from app.nut.scanner import scan_usb_ups

router = APIRouter(prefix="/api/nut", tags=["nut"])


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
        services = await request.app.state.supervisor.get_services_status()
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
    return await request.app.state.supervisor.get_services_status()


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
    services = await supervisor.get_services_status()
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

    return NutClientsOverview(
        server_ip=detect_local_lan_ip(),
        nut_port=settings.nut_port,
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
