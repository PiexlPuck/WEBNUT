"""System metadata, runtime role info, and in-app version check endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import settings
from app.models.schemas import AppVersionStatus, SystemInfoResponse
from app.services.version_checker import read_local_version

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info", response_model=SystemInfoResponse)
async def get_system_info(request: Request) -> SystemInfoResponse:
    role = settings.webnut_role
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
    )


@router.get("/version", response_model=AppVersionStatus)
async def get_version_status(request: Request) -> AppVersionStatus:
    return await request.app.state.version_checker.get_status()


@router.post("/version/check", response_model=AppVersionStatus)
async def check_for_updates(request: Request) -> AppVersionStatus:
    return await request.app.state.version_checker.check_now()

