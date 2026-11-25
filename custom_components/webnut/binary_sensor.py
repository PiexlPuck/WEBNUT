"""Binary sensor platform for WebNUT."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WebNutConfigEntry, WebNutCoordinator
from .entity import WebNutHostEntity, WebNutServerEntity, WebNutUpsEntity


@dataclass(frozen=True, kw_only=True)
class WebNutBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


UPS_BINARY: tuple[WebNutBinaryDescription, ...] = (
    WebNutBinaryDescription(
        key="on_battery",
        translation_key="on_battery",
        icon="mdi:battery-arrow-down",
        value_fn=lambda u: bool(u.get("on_battery")),
    ),
    WebNutBinaryDescription(
        key="low_battery",
        translation_key="low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        value_fn=lambda u: bool(u.get("low_battery")),
    ),
)

SERVER_BINARY: tuple[WebNutBinaryDescription, ...] = (
    WebNutBinaryDescription(
        key="ups_connected",
        translation_key="ups_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: bool(d.get("connected")),
    ),
    WebNutBinaryDescription(
        key="repeater_running",
        translation_key="repeater_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.get("repeater_running")),
    ),
    WebNutBinaryDescription(
        key="update_available",
        translation_key="update_available",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.get("update_available")),
    ),
)

HOST_BINARY: tuple[WebNutBinaryDescription, ...] = (
    WebNutBinaryDescription(
        key="online",
        translation_key="host_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda h: bool(h.get("is_online")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebNutConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(WebNutServerBinary(coordinator, d) for d in SERVER_BINARY)

    known_ups: set[str] = set()
    known_hosts: set[int] = set()

    @callback
    def _add_new() -> None:
        new: list[BinarySensorEntity] = []
        for ups_name in coordinator.data.ups:
            if ups_name not in known_ups:
                known_ups.add(ups_name)
                new.extend(WebNutUpsBinary(coordinator, ups_name, d) for d in UPS_BINARY)
        for host_id in coordinator.data.hosts:
            if host_id not in known_hosts:
                known_hosts.add(host_id)
                new.extend(WebNutHostBinary(coordinator, host_id, d) for d in HOST_BINARY)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WebNutServerBinary(WebNutServerEntity, BinarySensorEntity):
    entity_description: WebNutBinaryDescription

    def __init__(self, coordinator: WebNutCoordinator, description: WebNutBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data.raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "update_available":
            raw = self.coordinator.data.raw
            return {"installed_version": raw.get("version"), "latest_version": raw.get("latest_version")}
        if self.entity_description.key == "ups_connected":
            return {"error": self.coordinator.data.raw.get("error")}
        return None


class WebNutUpsBinary(WebNutUpsEntity, BinarySensorEntity):
    entity_description: WebNutBinaryDescription

    def __init__(
        self, coordinator: WebNutCoordinator, ups_name: str, description: WebNutBinaryDescription
    ) -> None:
        super().__init__(coordinator, ups_name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        ups = self.ups
        return self.entity_description.value_fn(ups) if ups else None


class WebNutHostBinary(WebNutHostEntity, BinarySensorEntity):
    entity_description: WebNutBinaryDescription

    def __init__(
        self, coordinator: WebNutCoordinator, host_id: int, description: WebNutBinaryDescription
    ) -> None:
        super().__init__(coordinator, host_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        host = self.host
        return self.entity_description.value_fn(host) if host else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        host = self.host
        if not host:
            return None
        return {
            "ip_or_host": host.get("ip_or_host"),
            "last_seen_at": host.get("last_seen_at"),
            "virtual_ups_name": host.get("virtual_ups_name"),
            "monitoring_enabled": host.get("enabled"),
        }
