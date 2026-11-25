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
    MonitorAccountRevealResponse,
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
    RepeaterStatusResponse,
    SavedMonitorAccount,
    ScannedSnmpDevice,
    ScannedUsbDevice,
    SnmpScanRequest,
    SnmpUpsSetupRequest,
    TelemetrySnapshot,
)
from app.nut.client import NutTcpClient
from app.nut.client_config import detect_local_lan_ip, generate_client_config
from app.nut.manager import NutConfigApplyError
from app.nut.parser import parse_nut_conf, parse_ups_conf, parse_upsd_conf, parse_upsmon_conf
from app.nut.scanner import scan_all_hardware, scan_snmp_ups, scan_usb_ups

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


@router.post("/scan-snmp", response_model=list[ScannedSnmpDevice])
async def scan_snmp(payload: SnmpScanRequest, request: Request) -> list[ScannedSnmpDevice]:
    event_log = request.app.state.event_log
    try:
        devices = await scan_snmp_ups(
            start_ip=payload.start_ip,
            end_ip=payload.end_ip,
            community=payload.community,
            snmp_version=payload.snmp_version,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await event_log.record(
        "nut_config",
        "info",
        "nut_snmp_scan",
        f"SNMP scan ({payload.start_ip} - {payload.end_ip}) discovered {len(devices)} UPS device(s)",
        {"count": len(devices)},
    )
    return devices


@router.post("/setup/snmp")
async def setup_snmp_ups(payload: SnmpUpsSetupRequest, request: Request) -> dict:
    """One-page SNMP UPS configurator: saves snmp-ups entry to ups.conf and reloads drivers/upsd."""
    manager = request.app.state.nut_manager
    parsed, _ = manager.read_config("ups.conf", mask_secrets=True)
    ups_list = [u for u in parsed.get("ups_list", []) if u.get("name") != payload.name.strip()]

    directives = [
        {"key": "mibs", "value": payload.mibs.strip() or "auto"},
        {"key": "snmp_version", "value": payload.snmp_version},
        {"key": "pollfreq", "value": str(payload.pollfreq)},
    ]
    if payload.snmp_version in ("v1", "v2c"):
        directives.append({"key": "community", "value": payload.community.strip() or "public"})
    else:
        directives.append({"key": "secLevel", "value": payload.sec_level})
        if payload.sec_name.strip():
            directives.append({"key": "secName", "value": payload.sec_name.strip()})
        if payload.sec_level in ("authNoPriv", "authPriv"):
            directives.append({"key": "authProtocol", "value": payload.auth_protocol})
            if payload.auth_password:
                directives.append({"key": "authPassword", "value": payload.auth_password})
        if payload.sec_level == "authPriv":
            directives.append({"key": "privProtocol", "value": payload.priv_protocol})
            if payload.priv_password:
                directives.append({"key": "privPassword", "value": payload.priv_password})

    ups_list.append(
        {
            "name": payload.name.strip(),
            "driver": "snmp-ups",
            "port": payload.host.strip(),
            "desc": payload.desc.strip() or f"SNMP UPS ({payload.host.strip()})",
            "directives": directives,
        }
    )
    parsed["ups_list"] = ups_list

    try:
        raw, applied, message = await manager.save_config("ups.conf", parsed, apply_reload=True)
        conn_row = await _get_connection_row(request.app.state.db)
        services = await request.app.state.supervisor.get_services_status(
            external_mode=(conn_row["mode"] == "external")
        )
        await request.app.state.ws_hub.broadcast("services", [s.model_dump() for s in services])
        return {"ok": True, "applied": applied, "message": message, "raw": raw}
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


@router.get("/repeater", response_model=RepeaterStatusResponse)
async def get_repeater_status(request: Request) -> RepeaterStatusResponse:
    repeater = request.app.state.repeater
    return RepeaterStatusResponse.model_validate(repeater.get_status())


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
    if payload.mode == "local":
        upsd_p = settings.webnut_nut_dir / "upsd.conf"
        target_port = settings.webnut_internal_nut_port
        if upsd_p.exists():
            upsd_cfg = parse_upsd_conf(upsd_p.read_text(encoding="utf-8", errors="replace"))
            if upsd_cfg.listens:
                target_port = int(upsd_cfg.listens[0].port)
    else:
        target_port = int(payload.port)
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

    host_rows = await db.fetch_all("SELECT id, name, ip_or_host, virtual_ups_name FROM hosts ORDER BY boot_priority ASC, id ASC")
    for hr in host_rows:
        vname = str(hr.get("virtual_ups_name") or "").strip()
        if vname and vname not in ups_names_set:
            ups_names_set.append(vname)

    available_users = [
        u["username"] for u in users_parsed.get("users", []) if u.get("username")
    ]

    ip_to_host = {
        str(r["ip_or_host"]).strip(): (int(r["id"]), str(r["name"])) for r in host_rows
    }

    connected_clients: list[ConnectedNutClient] = []
    seen_pairs: set[tuple[str, str]] = set()
    repeater = getattr(request.app.state, "repeater", None)
    if repeater is not None:
        for rc in repeater.get_connected_clients():
            key = (str(rc["ups_name"]), str(rc["client_ip"]))
            if key not in seen_pairs:
                seen_pairs.add(key)
                matched = ip_to_host.get(key[1])
                connected_clients.append(
                    ConnectedNutClient(
                        ups_name=key[0],
                        client_ip=key[1],
                        matched_host_id=matched[0] if matched else None,
                        matched_host_name=matched[1] if matched else None,
                    )
                )

    for u in telemetry.get("ups_list", []):
        uname = str(u.get("name", ""))
        for cip in u.get("connected_clients", []) or []:
            key = (uname, str(cip))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            matched = ip_to_host.get(str(cip))
            connected_clients.append(
                ConnectedNutClient(
                    ups_name=uname,
                    client_ip=str(cip),
                    matched_host_id=matched[0] if matched else None,
                    matched_host_name=matched[1] if matched else None,
                )
            )

    # All LAN clients always connect to WebNUT's Smart NUT Repeater (even when upstream is external)
    orch_cfg = await orchestrator.get_config()
    repeater_port = int(orch_cfg.repeater_port) if orch_cfg.repeater_enabled else settings.nut_port

    saved_rows = await db.fetch_all(
        "SELECT username, role, notes, password_enc, updated_at FROM nut_client_accounts ORDER BY updated_at DESC, username ASC"
    )
    saved_accounts: list[SavedMonitorAccount] = []
    seen_saved: set[str] = set()
    for sr in saved_rows:
        uname = str(sr["username"])
        seen_saved.add(uname)
        if uname not in available_users:
            available_users.append(uname)
        saved_accounts.append(
            SavedMonitorAccount(
                username=uname,
                role=str(sr.get("role") or "secondary"),
                notes=str(sr.get("notes") or ""),
                has_password=bool(sr.get("password_enc")),
                source="saved",
                updated_at=sr.get("updated_at"),
            )
        )

    for u in users_parsed.get("users", []):
        uname = str(u.get("username") or "").strip()
        if not uname or uname == "admin" or uname in seen_saved:
            continue
        seen_saved.add(uname)
        saved_accounts.append(
            SavedMonitorAccount(
                username=uname,
                role=str(u.get("upsmon_role") or "secondary") or "secondary",
                notes="Defined in upsd.users",
                has_password=bool(u.get("password")),
                source="upsd",
                updated_at=None,
            )
        )

    return NutClientsOverview(
        server_ip=detect_local_lan_ip(),
        nut_port=repeater_port,
        ups_names=ups_names_set,
        available_users=available_users,
        saved_accounts=saved_accounts,
        connected_clients=connected_clients,
    )


@router.post("/clients/create-user", response_model=CreateSecondaryUserResponse)
async def create_secondary_nut_user(
    payload: CreateSecondaryUserRequest,
    request: Request,
) -> CreateSecondaryUserResponse:
    manager = request.app.state.nut_manager
    db = request.app.state.db
    username = payload.username.strip()
    password = payload.password.strip() or secrets.token_urlsafe(16)
    notes = payload.notes.strip()

    conn_row = await _get_connection_row(db)
    apply_reload = conn_row["mode"] != "external"

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
            apply_reload=apply_reload,
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

    secret_box = request.app.state.secret_box
    password_enc = secret_box.encrypt(password)
    now = utc_now_iso()
    await db.execute(
        """
        INSERT INTO nut_client_accounts (username, password_enc, role, notes, created_at, updated_at)
        VALUES (?, ?, 'secondary', ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            password_enc = excluded.password_enc,
            role = excluded.role,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (username, password_enc, notes, now, now),
    )

    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "nut_config",
            "info",
            "monitor_account_saved",
            f"Saved secondary NUT monitor account '{username}'",
            {"username": username, "notes": notes, "applied": applied},
        )

    return CreateSecondaryUserResponse(
        username=username,
        password=password,
        notes=notes,
        upsmon_role="secondary",
        applied=applied,
        message=message,
    )


@router.get("/clients/accounts/{username}/reveal", response_model=MonitorAccountRevealResponse)
async def reveal_monitor_account(username: str, request: Request) -> MonitorAccountRevealResponse:
    target = username.strip()
    db = request.app.state.db
    row = await db.fetch_one(
        "SELECT username, password_enc, role, notes FROM nut_client_accounts WHERE username = ?",
        (target,),
    )
    if row is not None:
        secret_box = request.app.state.secret_box
        return MonitorAccountRevealResponse(
            username=str(row["username"]),
            password=secret_box.decrypt(str(row.get("password_enc") or "")),
            role=str(row.get("role") or "secondary"),
            notes=str(row.get("notes") or ""),
        )

    manager = request.app.state.nut_manager
    parsed_users, _ = manager.read_config("upsd.users", mask_secrets=False)
    for entry in parsed_users.get("users", []):
        if entry.get("username") == target and target != "admin":
            return MonitorAccountRevealResponse(
                username=target,
                password=str(entry.get("password") or ""),
                role=str(entry.get("upsmon_role") or "secondary") or "secondary",
                notes="Defined in upsd.users",
            )

    raise HTTPException(status_code=404, detail=f"Monitor account '{target}' not found")


@router.delete("/clients/accounts/{username}")
async def delete_monitor_account(username: str, request: Request) -> dict:
    target = username.strip()
    if target == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete the primary 'admin' NUT user")

    db = request.app.state.db
    manager = request.app.state.nut_manager
    existing_db = await db.fetch_one(
        "SELECT id FROM nut_client_accounts WHERE username = ?", (target,)
    )
    parsed_users, _ = manager.read_config("upsd.users", mask_secrets=True)
    users_list = list(parsed_users.get("users", []))
    remaining_users = [u for u in users_list if u.get("username") != target]
    removed_from_file = len(remaining_users) != len(users_list)

    if existing_db is None and not removed_from_file:
        raise HTTPException(status_code=404, detail=f"Monitor account '{target}' not found")

    if existing_db is not None:
        await db.execute("DELETE FROM nut_client_accounts WHERE username = ?", (target,))

    if removed_from_file:
        conn_row = await _get_connection_row(db)
        parsed_users["users"] = remaining_users
        await manager.save_config(
            "upsd.users",
            parsed_users,
            apply_reload=(conn_row["mode"] != "external"),
        )

    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "nut_config",
            "info",
            "monitor_account_deleted",
            f"Deleted secondary NUT monitor account '{target}'",
            {"username": target},
        )

    return {"deleted": True, "username": target}


@router.post("/clients/generate", response_model=NutClientConfigResponse)
async def generate_nut_client_config(payload: NutClientConfigRequest) -> NutClientConfigResponse:
    try:
        return generate_client_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
