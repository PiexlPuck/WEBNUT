"""Native Pushover notification provider."""

from __future__ import annotations

import httpx

from app.notifications.base import DeliveryResult, NotificationMessage, NotificationProvider

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class PushoverProvider(NotificationProvider):
    """Delivers alerts via the Pushover HTTPS Messages API."""

    provider_type = "pushover"

    def __init__(
        self,
        user_key: str,
        api_token: str,
        device: str = "",
        sound: str = "",
        emergency_retry_sec: int = 60,
        emergency_expire_sec: int = 3600,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.user_key = user_key.strip()
        self.api_token = api_token.strip()
        self.device = device.strip()
        self.sound = sound.strip()
        self.emergency_retry_sec = max(30, min(3600, int(emergency_retry_sec)))
        self.emergency_expire_sec = max(60, min(10800, int(emergency_expire_sec)))
        self._external_client = http_client

    def build_payload(self, message: NotificationMessage) -> dict[str, str | int]:
        priority = max(-2, min(2, int(message.priority)))
        payload: dict[str, str | int] = {
            "token": self.api_token,
            "user": self.user_key,
            "title": message.title,
            "message": message.body,
            "priority": priority,
        }
        if self.device:
            payload["device"] = self.device
        if self.sound:
            payload["sound"] = self.sound
        if priority == 2:
            payload["retry"] = self.emergency_retry_sec
            payload["expire"] = self.emergency_expire_sec
        return payload

    def _sanitize(self, text: str) -> str:
        sanitized = text
        for secret in (self.api_token, self.user_key):
            if secret:
                sanitized = sanitized.replace(secret, "[REDACTED]")
        return sanitized

    async def send(self, message: NotificationMessage) -> DeliveryResult:
        if not self.user_key or not self.api_token:
            return DeliveryResult(
                provider=self.provider_type,
                success=False,
                error="Pushover user_key and api_token must be configured",
            )

        payload = self.build_payload(message)
        try:
            if self._external_client is not None:
                resp = await self._external_client.post(PUSHOVER_API_URL, data=payload, timeout=10.0)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(PUSHOVER_API_URL, data=payload)
            if 200 <= resp.status_code < 300:
                return DeliveryResult(
                    provider=self.provider_type,
                    success=True,
                    status_code=resp.status_code,
                    response_body=self._sanitize(resp.text),
                )
            return DeliveryResult(
                provider=self.provider_type,
                success=False,
                status_code=resp.status_code,
                error=f"Pushover HTTP {resp.status_code}: {self._sanitize(resp.text)}",
                response_body=self._sanitize(resp.text),
            )
        except Exception as exc:
            return DeliveryResult(
                provider=self.provider_type,
                success=False,
                error=self._sanitize(str(exc)),
            )
