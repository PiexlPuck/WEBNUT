"""Filterable event & audit log API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.models.schemas import EventLogPage

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=EventLogPage)
async def list_events(
    request: Request,
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EventLogPage:
    return await request.app.state.event_log.query(
        category=category,
        severity=severity,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.delete("")
async def clear_events(request: Request) -> dict:
    deleted = await request.app.state.event_log.clear()
    return {"cleared": deleted}
