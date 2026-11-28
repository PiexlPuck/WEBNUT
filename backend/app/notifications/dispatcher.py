"""Notification dispatcher managing event rules, provider configs, and audit logs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.database import Database, utc_now_iso
from app.models.schemas import (
    SECRET_MASK,
    NotificationRuleItem,
    NotificationSettingsResponse,
    PushoverConfigRequest,
    PushoverConfigView,
)
from app.notifications.base import DeliveryResult, NotificationMessage, NotificationProvider
from app.notifications.pushover import PushoverProvider
from app.services.event_log import EventLogService


class NotificationDispatcher:
    """Routes system events to configured NotificationProvider instances."""

    def __init__(
        self,
        db: Database,
        event_log: EventLogService,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.db = db
        self.event_log = event_log
        self.http_client = http_client

    async def _get_pushover_row(self) -> tuple[bool, dict[str, Any]]:
        row = await self.db.fetch_one(
            "SELECT enabled, config_json FROM notification_providers WHERE provider_type = 'pushover'"
        )
        if row is None:
            return False, {}
        cfg = json.loads(row["config_json"] or "{}")
        return bool(row["enabled"]), cfg

    async def get_settings(self) -> NotificationSettingsResponse:
        enabled, cfg = await self._get_pushover_row()
        user_key = str(cfg.get("user_key", ""))
        api_token = str(cfg.get("api_token", ""))
        view = PushoverConfigView(
            enabled=enabled,
            user_key_configured=bool(user_key),
            api_token_configured=bool(api_token),
            user_key=SECRET_MASK if user_key else "",
            api_token=SECRET_MASK if api_token else "",
            device=str(cfg.get("device", "")),
            sound=str(cfg.get("sound", "")),
            emergency_retry_sec=int(cfg.get("emergency_retry_sec", 60)),
            emergency_expire_sec=int(cfg.get("emergency_expire_sec", 3600)),
        )
        rules_rows = await self.db.fetch_all(
            "SELECT event_type, enabled, priority FROM notification_rules ORDER BY event_type ASC"
        )
        rules = [
            NotificationRuleItem(
                event_type=r["event_type"],
                enabled=bool(r["enabled"]),
                priority=int(r["priority"]),
            )
            for r in rules_rows
        ]
        return NotificationSettingsResponse(pushover=view, rules=rules)

    async def update_pushover_config(self, req: PushoverConfigRequest) -> PushoverConfigView:
        _, existing_cfg = await self._get_pushover_row()
        user_key = (
            existing_cfg.get("user_key", "")
            if req.user_key == SECRET_MASK
            else req.user_key.strip()
        )
        api_token = (
            existing_cfg.get("api_token", "")
            if req.api_token == SECRET_MASK
            else req.api_token.strip()
        )
        new_cfg = {
            "user_key": user_key,
            "api_token": api_token,
            "device": req.device.strip(),
            "sound": req.sound.strip(),
            "emergency_retry_sec": req.emergency_retry_sec,
            "emergency_expire_sec": req.emergency_expire_sec,
        }
        now = utc_now_iso()
        await self.db.execute(
            """
            INSERT INTO notification_providers (provider_type, enabled, config_json, updated_at)
            VALUES ('pushover', ?, ?, ?)
            ON CONFLICT(provider_type) DO UPDATE SET
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (1 if req.enabled else 0, json.dumps(new_cfg), now),
        )
        await self.event_log.record(
            "notification",
            "info",
            "notification_config_updated",
            f"Updated Pushover settings (enabled={req.enabled})",
        )
        settings = await self.get_settings()
        return settings.pushover

    async def update_rules(self, rules: list[NotificationRuleItem]) -> list[NotificationRuleItem]:
        now = utc_now_iso()
        for rule in rules:
            await self.db.execute(
                """
                INSERT INTO notification_rules (event_type, enabled, priority, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(event_type) DO UPDATE SET
                    enabled = excluded.enabled,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (rule.event_type, 1 if rule.enabled else 0, rule.priority, now),
            )
        await self.event_log.record(
            "notification",
            "info",
            "notification_rules_updated",
            f"Updated {len(rules)} notification event rules",
        )
        settings = await self.get_settings()
        return settings.rules

    async def _build_enabled_providers(self) -> list[NotificationProvider]:
        providers: list[NotificationProvider] = []
        enabled, cfg = await self._get_pushover_row()
        if enabled:
            providers.append(
                PushoverProvider(
                    user_key=str(cfg.get("user_key", "")),
                    api_token=str(cfg.get("api_token", "")),
                    device=str(cfg.get("device", "")),
                    sound=str(cfg.get("sound", "")),
                    emergency_retry_sec=int(cfg.get("emergency_retry_sec", 60)),
                    emergency_expire_sec=int(cfg.get("emergency_expire_sec", 3600)),
                    http_client=self.http_client,
                )
            )
        return providers

    async def dispatch_event(
        self,
        event_type: str,
        title: str,
        body: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[DeliveryResult]:
        rule = await self.db.fetch_one(
            "SELECT enabled, priority FROM notification_rules WHERE event_type = ?",
            (event_type,),
        )
        if rule is not None and not bool(rule["enabled"]):
            return []

        priority = int(rule["priority"]) if rule is not None else 0
        providers = await self._build_enabled_providers()
        if not providers:
            return []

        effective_title = title
        try:
            ob_row = await self.db.fetch_one(
                "SELECT instance_name FROM onboarding_state WHERE id = 1"
            )
            if ob_row:
                inst_name = str(ob_row.get("instance_name") or "").strip()
                if (
                    inst_name
                    and inst_name.lower() != "webnut"
                    and inst_name.lower() not in title.lower()
                ):
                    effective_title = f"[{inst_name}] {title}"
        except Exception:
            pass

        msg = NotificationMessage(
            event_type=event_type,
            title=effective_title,
            body=body,
            priority=priority,
            metadata=metadata or {},
        )
        results = await asyncio.gather(*(p.send(msg) for p in providers))
        for res in results:
            await self.event_log.record(
                "notification",
                "success" if res.success else "error",
                "notification_sent" if res.success else "notification_failed",
                f"{res.provider} notification for '{event_type}': "
                + ("delivered" if res.success else (res.error or "failed")),
                {
                    "provider": res.provider,
                    "event_type": event_type,
                    "priority": priority,
                    "status_code": res.status_code,
                    "error": res.error,
                },
            )
        return list(results)

    async def send_test(self, provider_type: str = "pushover", priority: int = 0) -> DeliveryResult:
        if provider_type != "pushover":
            return DeliveryResult(
                provider=provider_type,
                success=False,
                error=f"Unsupported provider '{provider_type}'",
            )
        _, cfg = await self._get_pushover_row()
        provider = PushoverProvider(
            user_key=str(cfg.get("user_key", "")),
            api_token=str(cfg.get("api_token", "")),
            device=str(cfg.get("device", "")),
            sound=str(cfg.get("sound", "")),
            emergency_retry_sec=int(cfg.get("emergency_retry_sec", 60)),
            emergency_expire_sec=int(cfg.get("emergency_expire_sec", 3600)),
            http_client=self.http_client,
        )
        msg = NotificationMessage(
            event_type="test_notification",
            title="WEBNUT Test Notification",
            body="Your WEBNUT Pushover integration is configured and working.",
            priority=priority,
        )
        res = await provider.send(msg)
        await self.event_log.record(
            "notification",
            "success" if res.success else "error",
            "notification_test",
            f"Test notification via {provider_type}: "
            + ("delivered" if res.success else (res.error or "failed")),
            {"provider": res.provider, "status_code": res.status_code, "error": res.error},
        )
        return res
