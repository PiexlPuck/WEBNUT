"""System metadata and in-app version check endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.models.schemas import AppVersionStatus

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/version", response_model=AppVersionStatus)
async def get_version_status(request: Request) -> AppVersionStatus:
    return await request.app.state.version_checker.get_status()


@router.post("/version/check", response_model=AppVersionStatus)
async def check_for_updates(request: Request) -> AppVersionStatus:
    return await request.app.state.version_checker.check_now()
