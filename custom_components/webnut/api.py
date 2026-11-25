"""Async HTTP client for the WebNUT Home Assistant integration API."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

REQUEST_TIMEOUT = 10


class WebNutError(Exception):
    """Base WebNUT API error."""


class WebNutConnectionError(WebNutError):
    """WebNUT could not be reached."""


class WebNutAuthError(WebNutError):
    """The WebNUT API key is missing or invalid."""


class WebNutDisabledError(WebNutError):
    """Home Assistant endpoints are disabled in WebNUT."""


class WebNutApiClient:
    """Minimal client for /api/integrations/homeassistant/* endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        api_key: str | None = None,
        use_ssl: bool = False,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        scheme = "https" if use_ssl else "http"
        self.base_url = f"{scheme}://{host.strip().rstrip('/')}:{int(port)}"
        self._api_key = (api_key or "").strip()
        self._verify_ssl = verify_ssl

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def _request(self, method: str, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    url,
                    headers=self._headers(),
                    ssl=None if self._verify_ssl else False,
                ) as resp:
                    if resp.status == 401:
                        raise WebNutAuthError("Invalid or missing WebNUT API key")
                    if resp.status == 403:
                        raise WebNutDisabledError(
                            "Home Assistant endpoints are disabled in WebNUT"
                        )
                    if resp.status >= 400:
                        text = await resp.text()
                        raise WebNutError(f"WebNUT returned HTTP {resp.status}: {text[:200]}")
                    return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise WebNutConnectionError(f"Cannot reach WebNUT at {url}: {err}") from err

    async def async_get_info(self) -> dict[str, Any]:
        return await self._request("GET", "/api/integrations/homeassistant/info")

    async def async_get_state(self) -> dict[str, Any]:
        return await self._request("GET", "/api/integrations/homeassistant/state")

    async def async_wake_host(self, host_id: int) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/integrations/homeassistant/hosts/{int(host_id)}/wake"
        )
