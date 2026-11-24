"""Tests for Pushover notification provider, event rules, secret masking, and audit logging."""

from __future__ import annotations

import httpx
import pytest

from app.models.schemas import SECRET_MASK, NotificationRuleItem, PushoverConfigRequest
from app.notifications.base import NotificationMessage
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.pushover import PushoverProvider


def test_pushover_payload_and_emergency_params() -> None:
    provider = PushoverProvider(
        user_key="u_key_123",
        api_token="a_tok_456",
        device="pixel8",
        sound="siren",
        emergency_retry_sec=45,
        emergency_expire_sec=1800,
    )

    normal_payload = provider.build_payload(
        NotificationMessage(event_type="ups_power_restored", title="OL", body="Restored", priority=0)
    )
    assert normal_payload["priority"] == 0
    assert normal_payload["device"] == "pixel8"
    assert "retry" not in normal_payload

    emerg_payload = provider.build_payload(
        NotificationMessage(event_type="host_wake_failed", title="FAIL", body="Down", priority=2)
    )
    assert emerg_payload["priority"] == 2
    assert emerg_payload["retry"] == 45
    assert emerg_payload["expire"] == 1800


@pytest.mark.asyncio
async def test_dispatcher_secret_masking_rules_and_delivery(db, event_log) -> None:
    captured_posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_posts.append({"url": str(request.url), "body": request.content.decode("utf-8")})
        return httpx.Response(200, json={"status": 1, "request": "abc"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = NotificationDispatcher(db, event_log, http_client=client)

        await dispatcher.update_pushover_config(
            PushoverConfigRequest(
                enabled=True,
                user_key="my_real_user_key",
                api_token="my_real_api_token",
                device="iphone",
            )
        )

        # Secrets must be masked on read
        settings = await dispatcher.get_settings()
        assert settings.pushover.user_key == SECRET_MASK
        assert settings.pushover.api_token == SECRET_MASK
        assert settings.pushover.user_key_configured is True

        # Updating device while keeping SECRET_MASK preserves real secrets
        await dispatcher.update_pushover_config(
            PushoverConfigRequest(
                enabled=True,
                user_key=SECRET_MASK,
                api_token=SECRET_MASK,
                device="ipad",
            )
        )

        # Disable ups_on_battery rule and verify it is suppressed
        await dispatcher.update_rules(
            [NotificationRuleItem(event_type="ups_on_battery", enabled=False, priority=1)]
        )
        res_disabled = await dispatcher.dispatch_event("ups_on_battery", "OB", "On battery")
        assert res_disabled == []
        assert len(captured_posts) == 0

        # Enabled event dispatches with rule priority (2 for host_wake_failed)
        res_enabled = await dispatcher.dispatch_event("host_wake_failed", "Failed", "Host down")
        assert len(res_enabled) == 1
        assert res_enabled[0].success is True
        assert len(captured_posts) == 1
        assert "user=my_real_user_key" in captured_posts[0]["body"]
        assert "token=my_real_api_token" in captured_posts[0]["body"]
        assert "device=ipad" in captured_posts[0]["body"]
        assert "priority=2" in captured_posts[0]["body"]

        # Test notification endpoint helper
        test_res = await dispatcher.send_test()
        assert test_res.success is True
        assert len(captured_posts) == 2

        # Audit log recorded deliveries without leaking secrets
        logs = await event_log.query(category="notification")
        assert logs.total >= 2
        for item in logs.items:
            assert "my_real_user_key" not in item.message
            assert "my_real_api_token" not in item.message
