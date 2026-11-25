"""Managed host registry, ARP MAC lookup, per-host policies, and SSH/SFTP deployment endpoints."""

from __future__ import annotations

import re
import secrets
from fastapi import APIRouter, HTTPException, Request

from app.database import fetch_public_hosts, utc_now_iso
from app.models.schemas import (
    SECRET_MASK,
    ArpLookupRequest,
    ArpLookupResponse,
    HostCreate,
    HostRecord,
    HostUpdate,
    ManualWakeRequest,
    SshDeployRequest,
    SshDeployResponse,
    SshTestRequest,
)
from app.nut.client_config import detect_local_lan_ip
from app.services.ssh_deployer import deploy_nut_client_ssh, test_ssh_connection
from app.wol.magic_packet import normalize_mac
from app.wol.network import resolve_mac_from_arp

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def _slugify_virtual_ups(name: str, fallback_id: int | None = None) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    if slug:
        return slug[:48]
    return f"host_{fallback_id or 1}"


def _row_to_host(row: dict) -> HostRecord:
    pw_enc = str(row.get("ssh_password_enc") or "")
    sudo_enc = str(row.get("ssh_sudo_password_enc") or "")
    return HostRecord(
        id=int(row["id"]),
        name=row["name"],
        ip_or_host=row["ip_or_host"],
        mac_address=row["mac_address"],
        subnet_broadcast=row["subnet_broadcast"],
        boot_priority=int(row["boot_priority"]),
        stagger_delay_sec=int(row["stagger_delay_sec"]),
        check_method=row["check_method"],
        check_port=row["check_port"],
        boot_timeout_sec=int(row["boot_timeout_sec"]),
        retry_interval_sec=int(row["retry_interval_sec"]),
        max_retries=int(row["max_retries"]),
        always_wake=bool(row["always_wake"]),
        enabled=bool(row["enabled"]),
        nut_client_enabled=bool(row.get("nut_client_enabled", 0)),
        nut_client_platform=row.get("nut_client_platform") or "linux_debian",
        nut_ups_name=row.get("nut_ups_name") or "",
        nut_client_user=row.get("nut_client_user") or "",
        virtual_ups_name=row.get("virtual_ups_name") or _slugify_virtual_ups(row["name"], row["id"]),
        wake_min_battery_charge=(
            float(row["wake_min_battery_charge"])
            if row.get("wake_min_battery_charge") is not None
            else None
        ),
        shutdown_mode=row.get("shutdown_mode") or "nut_stream",
        shutdown_battery_pct=(
            float(row["shutdown_battery_pct"])
            if row.get("shutdown_battery_pct") is not None
            else None
        ),
        shutdown_ob_delay_sec=(
            int(row["shutdown_ob_delay_sec"])
            if row.get("shutdown_ob_delay_sec") is not None
            else None
        ),
        disconnect_policy=row.get("disconnect_policy") or "inherit",
        disconnect_grace_sec=(
            int(row["disconnect_grace_sec"])
            if row.get("disconnect_grace_sec") is not None
            else None
        ),
        ssh_port=int(row.get("ssh_port") or 22),
        ssh_username=str(row.get("ssh_username") or ""),
        ssh_password=SECRET_MASK if pw_enc else "",
        ssh_sudo_password=SECRET_MASK if sudo_enc else "",
        ssh_shutdown_cmd=str(row.get("ssh_shutdown_cmd") or "sudo -S shutdown -h now"),
        ssh_password_configured=bool(pw_enc),
        is_online=bool(row["is_online"]),
        last_seen_at=row["last_seen_at"],
        was_online_before_outage=bool(row["was_online_before_outage"]),
        wake_state=row["wake_state"],
        wake_attempt=int(row["wake_attempt"]),
        last_wake_error=row["last_wake_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[HostRecord])
async def list_hosts(request: Request) -> list[HostRecord]:
    rows = await request.app.state.db.fetch_all(
        "SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC"
    )
    return [_row_to_host(r) for r in rows]


@router.post("", response_model=HostRecord, status_code=201)
async def create_host(payload: HostCreate, request: Request) -> HostRecord:
    try:
        mac = normalize_mac(payload.mac_address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.check_method == "tcp" and not payload.check_port:
        raise HTTPException(status_code=422, detail="check_port is required when check_method is 'tcp'")

    secret_box = request.app.state.secret_box
    pw_enc = secret_box.encrypt(payload.ssh_password) if payload.ssh_password else ""
    sudo_enc = secret_box.encrypt(payload.ssh_sudo_password) if payload.ssh_sudo_password else ""
    v_ups = payload.virtual_ups_name.strip() or _slugify_virtual_ups(payload.name)

    now = utc_now_iso()
    db = request.app.state.db
    host_id = await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, subnet_broadcast, boot_priority,
            stagger_delay_sec, check_method, check_port, boot_timeout_sec,
            retry_interval_sec, max_retries, always_wake, enabled,
            nut_client_enabled, nut_client_platform, nut_ups_name, nut_client_user,
            virtual_ups_name, wake_min_battery_charge, shutdown_mode,
            shutdown_battery_pct, shutdown_ob_delay_sec, disconnect_policy,
            disconnect_grace_sec, ssh_port, ssh_username, ssh_password_enc,
            ssh_sudo_password_enc, ssh_shutdown_cmd,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name.strip(),
            payload.ip_or_host.strip(),
            mac,
            payload.subnet_broadcast,
            payload.boot_priority,
            payload.stagger_delay_sec,
            payload.check_method,
            payload.check_port,
            payload.boot_timeout_sec,
            payload.retry_interval_sec,
            payload.max_retries,
            1 if payload.always_wake else 0,
            1 if payload.enabled else 0,
            1 if payload.nut_client_enabled else 0,
            payload.nut_client_platform,
            payload.nut_ups_name.strip(),
            payload.nut_client_user.strip(),
            v_ups,
            payload.wake_min_battery_charge,
            payload.shutdown_mode,
            payload.shutdown_battery_pct,
            payload.shutdown_ob_delay_sec,
            payload.disconnect_policy,
            payload.disconnect_grace_sec,
            payload.ssh_port,
            payload.ssh_username.strip(),
            pw_enc,
            sudo_enc,
            payload.ssh_shutdown_cmd.strip() or "sudo -S shutdown -h now",
            now,
            now,
        ),
    )
    await request.app.state.event_log.record(
        "hosts",
        "info",
        "host_created",
        f"Added managed host '{payload.name}' ({payload.ip_or_host}, {mac})",
        {"host_id": host_id, "virtual_ups": v_ups},
    )
    row = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    all_rows = await fetch_public_hosts(db)
    await request.app.state.ws_hub.broadcast("hosts", all_rows)
    return _row_to_host(row)  # type: ignore[arg-type]


@router.put("/{host_id}", response_model=HostRecord)
async def update_host(host_id: int, payload: HostUpdate, request: Request) -> HostRecord:
    db = request.app.state.db
    existing = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if existing is None:
        raise HTTPException(status_code=404, detail="Host not found")

    try:
        mac = normalize_mac(payload.mac_address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.check_method == "tcp" and not payload.check_port:
        raise HTTPException(status_code=422, detail="check_port is required when check_method is 'tcp'")

    secret_box = request.app.state.secret_box
    pw_enc = (
        str(existing.get("ssh_password_enc") or "")
        if payload.ssh_password == SECRET_MASK
        else (secret_box.encrypt(payload.ssh_password) if payload.ssh_password else "")
    )
    sudo_enc = (
        str(existing.get("ssh_sudo_password_enc") or "")
        if payload.ssh_sudo_password == SECRET_MASK
        else (secret_box.encrypt(payload.ssh_sudo_password) if payload.ssh_sudo_password else "")
    )
    v_ups = payload.virtual_ups_name.strip() or _slugify_virtual_ups(payload.name, host_id)

    now = utc_now_iso()
    await db.execute(
        """
        UPDATE hosts
        SET name = ?,
            ip_or_host = ?,
            mac_address = ?,
            subnet_broadcast = ?,
            boot_priority = ?,
            stagger_delay_sec = ?,
            check_method = ?,
            check_port = ?,
            boot_timeout_sec = ?,
            retry_interval_sec = ?,
            max_retries = ?,
            always_wake = ?,
            enabled = ?,
            nut_client_enabled = ?,
            nut_client_platform = ?,
            nut_ups_name = ?,
            nut_client_user = ?,
            virtual_ups_name = ?,
            wake_min_battery_charge = ?,
            shutdown_mode = ?,
            shutdown_battery_pct = ?,
            shutdown_ob_delay_sec = ?,
            disconnect_policy = ?,
            disconnect_grace_sec = ?,
            ssh_port = ?,
            ssh_username = ?,
            ssh_password_enc = ?,
            ssh_sudo_password_enc = ?,
            ssh_shutdown_cmd = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name.strip(),
            payload.ip_or_host.strip(),
            mac,
            payload.subnet_broadcast,
            payload.boot_priority,
            payload.stagger_delay_sec,
            payload.check_method,
            payload.check_port,
            payload.boot_timeout_sec,
            payload.retry_interval_sec,
            payload.max_retries,
            1 if payload.always_wake else 0,
            1 if payload.enabled else 0,
            1 if payload.nut_client_enabled else 0,
            payload.nut_client_platform,
            payload.nut_ups_name.strip(),
            payload.nut_client_user.strip(),
            v_ups,
            payload.wake_min_battery_charge,
            payload.shutdown_mode,
            payload.shutdown_battery_pct,
            payload.shutdown_ob_delay_sec,
            payload.disconnect_policy,
            payload.disconnect_grace_sec,
            payload.ssh_port,
            payload.ssh_username.strip(),
            pw_enc,
            sudo_enc,
            payload.ssh_shutdown_cmd.strip() or "sudo -S shutdown -h now",
            now,
            host_id,
        ),
    )
    await request.app.state.event_log.record(
        "hosts",
        "info",
        "host_updated",
        f"Updated managed host '{payload.name}' ({payload.ip_or_host})",
        {"host_id": host_id, "virtual_ups": v_ups},
    )
    row = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    all_rows = await fetch_public_hosts(db)
    await request.app.state.ws_hub.broadcast("hosts", all_rows)
    return _row_to_host(row)  # type: ignore[arg-type]


@router.delete("/{host_id}")
async def delete_host(host_id: int, request: Request) -> dict:
    db = request.app.state.db
    existing = await db.fetch_one("SELECT name FROM hosts WHERE id = ?", (host_id,))
    if existing is None:
        raise HTTPException(status_code=404, detail="Host not found")
    await db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    await request.app.state.event_log.record(
        "hosts",
        "info",
        "host_deleted",
        f"Deleted managed host '{existing['name']}'",
        {"host_id": host_id},
    )
    all_rows = await fetch_public_hosts(db)
    await request.app.state.ws_hub.broadcast("hosts", all_rows)
    return {"deleted": True, "id": host_id}


@router.post("/arp-lookup", response_model=ArpLookupResponse)
async def arp_lookup(payload: ArpLookupRequest) -> ArpLookupResponse:
    return await resolve_mac_from_arp(payload.ip_or_host)


@router.post("/{host_id}/wake")
async def wake_host_now(
    host_id: int,
    payload: ManualWakeRequest,
    request: Request,
) -> dict:
    orchestrator = request.app.state.orchestrator
    try:
        await orchestrator.manual_wake_host(host_id, track_retries=payload.track_retries)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"started": True, "host_id": host_id, "track_retries": payload.track_retries}


@router.post("/{host_id}/test-ssh")
async def test_host_ssh(host_id: int, payload: SshTestRequest, request: Request) -> dict:
    db = request.app.state.db
    host = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    secret_box = request.app.state.secret_box
    password = (
        secret_box.decrypt(str(host.get("ssh_password_enc") or ""))
        if payload.ssh_password == SECRET_MASK or not payload.ssh_password
        else payload.ssh_password
    )
    if not password:
        raise HTTPException(status_code=422, detail="SSH password is required")

    ok, detail = await test_ssh_connection(
        str(host["ip_or_host"]),
        payload.ssh_port,
        payload.ssh_username.strip(),
        password,
    )
    return {"ok": ok, "detail": detail}


@router.post("/{host_id}/deploy-client", response_model=SshDeployResponse)
async def deploy_host_nut_client(
    host_id: int,
    payload: SshDeployRequest,
    request: Request,
) -> SshDeployResponse:
    db = request.app.state.db
    host = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found")

    secret_box = request.app.state.secret_box
    ssh_password = (
        secret_box.decrypt(str(host.get("ssh_password_enc") or ""))
        if payload.ssh_password == SECRET_MASK or not payload.ssh_password
        else payload.ssh_password
    )
    sudo_password = (
        secret_box.decrypt(str(host.get("ssh_sudo_password_enc") or ""))
        if payload.ssh_sudo_password == SECRET_MASK
        else payload.ssh_sudo_password
    )
    if not ssh_password:
        raise HTTPException(status_code=422, detail="SSH password is required")

    virtual_ups = _slugify_virtual_ups(host["name"], host_id)
    if str(host.get("virtual_ups_name") or "").strip():
        virtual_ups = str(host["virtual_ups_name"]).strip()

    orch_cfg = await request.app.state.orchestrator.get_config()
    repeater_ip = detect_local_lan_ip()
    repeater_port = int(orch_cfg.repeater_port)
    nut_username = payload.nut_username.strip()
    raw_nut_pw = payload.nut_password.strip()
    nut_password = ""
    if raw_nut_pw and raw_nut_pw != SECRET_MASK:
        nut_password = raw_nut_pw
    else:
        acct_row = await db.fetch_one(
            "SELECT password_enc FROM nut_client_accounts WHERE username = ?",
            (nut_username,),
        )
        if acct_row and acct_row.get("password_enc"):
            nut_password = secret_box.decrypt(str(acct_row["password_enc"]))
    if not nut_password:
        nut_password = secrets.token_urlsafe(16)

    result = await deploy_nut_client_ssh(
        host=str(host["ip_or_host"]),
        port=payload.ssh_port,
        ssh_username=payload.ssh_username.strip(),
        ssh_password=ssh_password,
        sudo_password=sudo_password,
        server_host=repeater_ip,
        nut_port=repeater_port,
        ups_name=virtual_ups,
        nut_username=payload.nut_username.strip(),
        nut_password=nut_password,
        final_delay=payload.final_delay,
        shutdown_cmd=payload.shutdown_cmd,
    )

    now = utc_now_iso()
    if payload.save_credentials:
        pw_enc = secret_box.encrypt(ssh_password)
        sudo_enc = secret_box.encrypt(sudo_password) if sudo_password else ""
        await db.execute(
            """
            UPDATE hosts
            SET ssh_port = ?, ssh_username = ?, ssh_password_enc = ?, ssh_sudo_password_enc = ?,
                nut_client_enabled = 1, virtual_ups_name = ?, nut_client_user = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.ssh_port,
                payload.ssh_username.strip(),
                pw_enc,
                sudo_enc,
                virtual_ups,
                payload.nut_username.strip(),
                now,
                host_id,
            ),
        )
    elif result.ok:
        await db.execute(
            """
            UPDATE hosts
            SET nut_client_enabled = 1, virtual_ups_name = ?, nut_client_user = ?, updated_at = ?
            WHERE id = ?
            """,
            (virtual_ups, payload.nut_username.strip(), now, host_id),
        )

    await request.app.state.event_log.record(
        "hosts",
        "success" if result.ok else "error",
        "ssh_client_deploy",
        (
            f"Deployed nut-client via SSH to '{host['name']}' pointing at {virtual_ups}@{repeater_ip}:{repeater_port}"
            if result.ok
            else f"SSH nut-client deployment failed for '{host['name']}': {result.error}"
        ),
        {"host_id": host_id, "virtual_ups": virtual_ups, "steps": result.steps},
    )
    all_rows = await fetch_public_hosts(db)
    await request.app.state.ws_hub.broadcast("hosts", all_rows)

    return SshDeployResponse(
        ok=result.ok,
        steps=result.steps,
        error=result.error,
        virtual_ups_name=virtual_ups,
        server_host=repeater_ip,
        nut_port=repeater_port,
    )
