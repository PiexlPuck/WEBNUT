"""Tests for in-app version checker and strict send-once Pushover alerting."""

from __future__ import annotations

import httpx
import pytest

from app.models.schemas import PushoverConfigRequest
from app.notifications.dispatcher import NotificationDispatcher
from app.services.version_checker import VersionChecker, is_newer_version, parse_semver


def test_semver_parsing_and_comparison() -> None:
    assert parse_semver("1.0.0") == (1, 0, 0)
    assert parse_semver("v2.3.14") == (2, 3, 14)
    assert parse_semver("invalid") is None

    assert is_newer_version("1.0.0", "1.0.1") is True
    assert is_newer_version("1.0.0", "v1.1.0") is True
    assert is_newer_version("1.2.0", "1.2.0") is False
    assert is_newer_version("1.2.0", "1.1.9") is False


@pytest.mark.asyncio
async def test_version_checker_release_and_send_once_guarantee(db, event_log) -> None:
    pushover_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com/repos/PiexlPuck/WEBNUT/releases/latest" in url:
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1.2.0",
                    "html_url": "https://github.com/PiexlPuck/WEBNUT/releases/tag/v1.2.0",
                    "body": "Added serial wizard",
                },
            )
        if "api.pushover.net/1/messages.json" in url:
            pushover_calls.append(request.content.decode("utf-8"))
            return httpx.Response(200, json={"status": 1, "request": "ok"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = NotificationDispatcher(db, event_log, http_client=client)
        await dispatcher.update_pushover_config(
            PushoverConfigRequest(
                enabled=True,
                user_key="user123",
                api_token="tok456",
            )
        )

        checker = VersionChecker(
            db,
            dispatcher,
            event_log,
            http_client=client,
            current_version="1.0.0",
        )

        # First check: detects v1.2.0 and sends Pushover notification ONCE
        st1 = await checker.check_now()
        assert st1.current_version == "1.0.0"
        assert st1.latest_version == "1.2.0"
        assert st1.update_available is True
        assert st1.notified_version == "1.2.0"
        assert st1.notified_at is not None
        assert len(pushover_calls) == 1
        assert "v1.2.0" in pushover_calls[0]

        # Second check: still shows update_available, MUST NOT send again
        st2 = await checker.check_now()
        assert st2.update_available is True
        assert len(pushover_calls) == 1

        # Simulated container restart with fresh VersionChecker instance: still MUST NOT send again
        checker2 = VersionChecker(
            db,
            dispatcher,
            event_log,
            http_client=client,
            current_version="1.0.0",
        )
        st3 = await checker2.check_now()
        assert st3.update_available is True
        assert len(pushover_calls) == 1


@pytest.mark.asyncio
async def test_version_checker_raw_version_fallback_when_no_release(db, event_log) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(404, json={"message": "Not Found"})
        if "raw.githubusercontent.com/PiexlPuck/WEBNUT/main/VERSION" in url:
            return httpx.Response(200, text="1.0.5\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = NotificationDispatcher(db, event_log, http_client=client)
        checker = VersionChecker(
            db,
            dispatcher,
            event_log,
            http_client=client,
            current_version="1.0.0",
        )
        st = await checker.check_now()
        assert st.latest_version == "1.0.5"
        assert st.update_available is True
        assert st.release_url == "https://github.com/PiexlPuck/WEBNUT"


@pytest.mark.asyncio
async def test_version_checker_falls_back_on_github_api_403_rate_limit(db, event_log) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(403, json={"message": "API rate limit exceeded"})
        if "raw.githubusercontent.com/PiexlPuck/WEBNUT/main/VERSION" in url:
            return httpx.Response(200, text="2.1.0\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = NotificationDispatcher(db, event_log, http_client=client)
        checker = VersionChecker(
            db,
            dispatcher,
            event_log,
            http_client=client,
            current_version="2.0.0",
        )
        st = await checker.check_now()
        assert st.last_error is None
        assert st.latest_version == "2.1.0"
        assert st.update_available is True


@pytest.mark.asyncio
async def test_version_checker_prefers_newer_raw_version_over_older_release(db, event_log) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com/repos/PiexlPuck/WEBNUT/releases/latest" in url:
            return httpx.Response(
                200,
                json={
                    "tag_name": "v1.0.0",
                    "html_url": "https://github.com/PiexlPuck/WEBNUT/releases/tag/v1.0.0",
                },
            )
        if "raw.githubusercontent.com/PiexlPuck/WEBNUT/main/VERSION" in url:
            return httpx.Response(200, text="2.1.0\n")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        dispatcher = NotificationDispatcher(db, event_log, http_client=client)
        checker = VersionChecker(
            db,
            dispatcher,
            event_log,
            http_client=client,
            current_version="2.0.0",
        )
        st = await checker.check_now()
        assert st.last_error is None
        assert st.latest_version == "2.1.0"
        assert st.update_available is True

