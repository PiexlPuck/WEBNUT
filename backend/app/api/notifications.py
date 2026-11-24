"""Notification provider configuration, event rules, and test delivery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    NotificationRuleItem,
    NotificationRulesUpdateRequest,
    NotificationSettingsResponse,
    NotificationTestRequest,
    PushoverConfigRequest,
    PushoverConfigView,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(request: Request) -> NotificationSettingsResponse:
    return await request.app.state.dispatcher.get_settings()


@router.put("/providers/pushover", response_model=PushoverConfigView)
async def update_pushover_settings(
    payload: PushoverConfigRequest,
    request: Request,
) -> PushoverConfigView:
    return await request.app.state.dispatcher.update_pushover_config(payload)


@router.put("/rules", response_model=list[NotificationRuleItem])
async def update_notification_rules(
    payload: NotificationRulesUpdateRequest,
    request: Request,
) -> list[NotificationRuleItem]:
    return await request.app.state.dispatcher.update_rules(payload.rules)


@router.post("/test")
async def send_test_notification(
    payload: NotificationTestRequest,
    request: Request,
) -> dict:
    res = await request.app.state.dispatcher.send_test(
        provider_type=payload.provider_type,
        priority=payload.priority,
    )
    if not res.success:
        raise HTTPException(status_code=400, detail=res.error or "Test notification failed")
    return {
        "success": True,
        "provider": res.provider,
        "status_code": res.status_code,
    }
