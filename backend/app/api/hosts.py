"""Managed host registry, ARP MAC auto-detection, and manual WoL endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.database import utc_now_iso
from app.models.schemas import (
    ArpLookupRequest,
    ArpLookupResponse,
    HostCreate,
    HostRecord,
    HostUpdate,
    ManualWakeRequest,
)
from app.wol.magic_packet import normalize_mac
from app.wol.network import resolve_mac_from_arp

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


def _row_to_host(row: dict) -> HostRecord:
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

    now = utc_now_iso()
    db = request.app.state.db
    host_id = await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, subnet_broadcast, boot_priority,
            stagger_delay_sec, check_method, check_port, boot_timeout_sec,
            retry_interval_sec, max_retries, always_wake, enabled,
            nut_client_enabled, nut_client_platform, nut_ups_name, nut_client_user,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            now,
            now,
        ),
    )
    await request.app.state.event_log.record(
        "hosts",
        "info",
        "host_created",
        f"Added managed host '{payload.name}' ({payload.ip_or_host}, {mac})",
        {"host_id": host_id},
    )
    row = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    all_rows = await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
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
            now,
            host_id,
        ),
    )
    await request.app.state.event_log.record(
        "hosts",
        "info",
        "host_updated",
        f"Updated managed host '{payload.name}' ({payload.ip_or_host})",
        {"host_id": host_id},
    )
    row = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (host_id,))
    all_rows = await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
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
    all_rows = await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
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
