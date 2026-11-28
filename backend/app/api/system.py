"""System metadata, instance identity, onboarding wizard, and in-app version check endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.database import utc_now_iso
from app.models.schemas import (
    AppVersionStatus,
    OnboardingStateResponse,
    OnboardingStateUpdateRequest,
    SystemIdentityUpdateRequest,
    SystemInfoResponse,
)
from app.services.version_checker import read_local_version

router = APIRouter(prefix="/api/system", tags=["system"])


async def _get_onboarding_row(db) -> dict[str, Any]:
    row = await db.fetch_one("SELECT * FROM onboarding_state WHERE id = 1")
    if row is not None:
        return row
    now = utc_now_iso()
    await db.execute(
        """
        INSERT OR IGNORE INTO onboarding_state (
            id, instance_name, completed, skipped, last_step, completed_at, updated_at
        ) VALUES (1, 'WebNUT', 0, 0, 0, NULL, ?)
        """,
        (now,),
    )
    row = await db.fetch_one("SELECT * FROM onboarding_state WHERE id = 1")
    assert row is not None
    return row


async def _build_system_info(request: Request) -> SystemInfoResponse:
    role = getattr(request.app.state, "role", settings.webnut_role)
    db = getattr(request.app.state, "db", None)
    instance_name = "WebNUT"
    if db is not None:
        row = await _get_onboarding_row(db)
        instance_name = str(row.get("instance_name") or "WebNUT").strip() or "WebNUT"

    node_name = ""
    paired = False
    agent = getattr(request.app.state, "repeater_agent", None)
    if agent is not None:
        link_status = await agent.get_link_status()
        node_name = link_status.node_name
        paired = link_status.paired

    return SystemInfoResponse(
        role=role,
        version=read_local_version(settings.webnut_version_file),
        web_port=settings.web_port,
        nut_port=settings.nut_port,
        node_name=node_name,
        paired=paired,
        instance_name=instance_name,
    )


async def _build_onboarding_response(request: Request) -> OnboardingStateResponse:
    db = request.app.state.db
    row = await _get_onboarding_row(db)
    role = getattr(request.app.state, "role", settings.webnut_role)
    instance_name = str(row.get("instance_name") or "WebNUT").strip() or "WebNUT"

    has_ups_configured = False
    nut_manager = getattr(request.app.state, "nut_manager", None)
    if nut_manager is not None:
        try:
            ups_parsed, _ = nut_manager.read_config("ups.conf", mask_secrets=True)
            if len(ups_parsed.get("ups_list") or []) > 0:
                has_ups_configured = True
        except Exception:
            pass
    if not has_ups_configured:
        conn_row = await db.fetch_one("SELECT mode FROM nut_connection_config WHERE id = 1")
        if conn_row and conn_row.get("mode") == "external":
            has_ups_configured = True

    has_ups_polling = False
    orchestrator = getattr(request.app.state, "orchestrator", None)
    agent = getattr(request.app.state, "repeater_agent", None)
    tel = None
    if orchestrator is not None:
        tel = orchestrator.latest_telemetry
    elif agent is not None:
        tel = agent.latest_telemetry
    if isinstance(tel, dict) and tel.get("connected") and len(tel.get("ups_list") or []) > 0:
        has_ups_polling = True

    hosts_row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM hosts")
    has_hosts = bool(hosts_row and int(hosts_row["cnt"]) > 0)

    has_pushover = False
    po_row = await db.fetch_one(
        "SELECT enabled, config_json FROM notification_providers WHERE provider_type = 'pushover'"
    )
    if po_row and bool(po_row.get("enabled")):
        try:
            cfg = json.loads(po_row.get("config_json") or "{}")
            has_pushover = bool(
                str(cfg.get("user_key") or "").strip()
                and str(cfg.get("api_token") or "").strip()
            )
        except json.JSONDecodeError:
            has_pushover = False

    rep_row = await db.fetch_one("SELECT COUNT(*) AS cnt FROM repeater_nodes")
    has_repeaters = bool(rep_row and int(rep_row["cnt"]) > 0)

    repeater_paired = False
    if agent is not None:
        link_status = await agent.get_link_status()
        repeater_paired = bool(link_status.paired)
    else:
        link_row = await db.fetch_one("SELECT paired FROM repeater_link_config WHERE id = 1")
        repeater_paired = bool(link_row and link_row.get("paired"))

    return OnboardingStateResponse(
        instance_name=instance_name,
        completed=bool(row.get("completed")),
        skipped=bool(row.get("skipped")),
        last_step=int(row.get("last_step") or 0),
        completed_at=row.get("completed_at"),
        role=role,
        has_ups_configured=has_ups_configured,
        has_ups_polling=has_ups_polling,
        has_hosts=has_hosts,
        has_pushover=has_pushover,
        has_repeaters=has_repeaters,
        repeater_paired=repeater_paired,
    )


@router.get("/info", response_model=SystemInfoResponse)
async def get_system_info(request: Request) -> SystemInfoResponse:
    return await _build_system_info(request)


@router.put("/identity", response_model=SystemInfoResponse)
async def update_system_identity(
    payload: SystemIdentityUpdateRequest, request: Request
) -> SystemInfoResponse:
    cleaned = payload.instance_name.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="instance_name cannot be empty")
    db = request.app.state.db
    await _get_onboarding_row(db)
    now = utc_now_iso()
    await db.execute(
        "UPDATE onboarding_state SET instance_name = ?, updated_at = ? WHERE id = 1",
        (cleaned, now),
    )
    event_log = getattr(request.app.state, "event_log", None)
    if event_log is not None:
        await event_log.record(
            "system",
            "info",
            "system_identity_updated",
            f"Updated WebNUT instance name to '{cleaned}'",
            {"instance_name": cleaned},
        )
    info = await _build_system_info(request)
    ws_hub = getattr(request.app.state, "ws_hub", None)
    if ws_hub is not None:
        await ws_hub.broadcast("system_info", info.model_dump())
    return info


@router.get("/onboarding", response_model=OnboardingStateResponse)
async def get_onboarding_state(request: Request) -> OnboardingStateResponse:
    return await _build_onboarding_response(request)


@router.put("/onboarding", response_model=OnboardingStateResponse)
async def update_onboarding_state(
    payload: OnboardingStateUpdateRequest, request: Request
) -> OnboardingStateResponse:
    db = request.app.state.db
    existing = await _get_onboarding_row(db)
    now = utc_now_iso()

    instance_name = (
        payload.instance_name.strip()
        if payload.instance_name is not None and payload.instance_name.strip()
        else str(existing.get("instance_name") or "WebNUT")
    )
    completed = (
        bool(payload.completed)
        if payload.completed is not None
        else bool(existing.get("completed"))
    )
    skipped = (
        bool(payload.skipped)
        if payload.skipped is not None
        else bool(existing.get("skipped"))
    )
    last_step = (
        int(payload.last_step)
        if payload.last_step is not None
        else int(existing.get("last_step") or 0)
    )
    completed_at = existing.get("completed_at")
    if payload.completed is True and not completed_at:
        completed_at = now
    elif payload.completed is False:
        completed_at = None

    await db.execute(
        """
        UPDATE onboarding_state
        SET instance_name = ?,
            completed = ?,
            skipped = ?,
            last_step = ?,
            completed_at = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            instance_name,
            1 if completed else 0,
            1 if skipped else 0,
            last_step,
            completed_at,
            now,
        ),
    )
    if payload.instance_name is not None:
        ws_hub = getattr(request.app.state, "ws_hub", None)
        if ws_hub is not None:
            info = await _build_system_info(request)
            await ws_hub.broadcast("system_info", info.model_dump())
    return await _build_onboarding_response(request)


@router.post("/onboarding/reset", response_model=OnboardingStateResponse)
async def reset_onboarding_state(request: Request) -> OnboardingStateResponse:
    db = request.app.state.db
    await _get_onboarding_row(db)
    now = utc_now_iso()
    await db.execute(
        """
        UPDATE onboarding_state
        SET completed = 0, skipped = 0, last_step = 0, completed_at = NULL, updated_at = ?
        WHERE id = 1
        """,
        (now,),
    )
    return await _build_onboarding_response(request)


@router.get("/version", response_model=AppVersionStatus)
async def get_version_status(request: Request) -> AppVersionStatus:
    return await request.app.state.version_checker.get_status()


@router.post("/version/check", response_model=AppVersionStatus)
async def check_for_updates(request: Request) -> AppVersionStatus:
    return await request.app.state.version_checker.check_now()
