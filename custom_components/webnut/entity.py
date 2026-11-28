"""Base entities and device helpers for WebNUT."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WebNutCoordinator


def server_device_info(coordinator: WebNutCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.instance_id)},
        name=coordinator.instance_name or "WebNUT",
        manufacturer=MANUFACTURER,
        model="UPS & Power Orchestrator",
        sw_version=coordinator.server_version,
        configuration_url=coordinator.client.base_url,
    )


class WebNutServerEntity(CoordinatorEntity[WebNutCoordinator]):
    """Entity attached to the WebNUT server device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WebNutCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.instance_id}_{key}"
        self._attr_device_info = server_device_info(coordinator)


class WebNutUpsEntity(CoordinatorEntity[WebNutCoordinator]):
    """Entity attached to a UPS device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WebNutCoordinator, ups_name: str, key: str) -> None:
        super().__init__(coordinator)
        self.ups_name = ups_name
        self._attr_unique_id = f"{coordinator.instance_id}_ups_{ups_name}_{key}"
        ups = coordinator.data.ups.get(ups_name, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.instance_id}_ups_{ups_name}")},
            name=ups.get("description") or f"UPS {ups_name}",
            manufacturer=ups.get("manufacturer") or MANUFACTURER,
            model=ups.get("model") or "NUT UPS",
            serial_number=None,
            via_device=(DOMAIN, coordinator.instance_id),
            configuration_url=coordinator.client.base_url,
        )

    @property
    def ups(self) -> dict[str, Any] | None:
        return self.coordinator.data.ups.get(self.ups_name)

    @property
    def available(self) -> bool:
        return super().available and self.ups is not None


class WebNutHostEntity(CoordinatorEntity[WebNutCoordinator]):
    """Entity attached to a managed host device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WebNutCoordinator, host_id: int, key: str) -> None:
        super().__init__(coordinator)
        self.host_id = host_id
        self._attr_unique_id = f"{coordinator.instance_id}_host_{host_id}_{key}"
        host = coordinator.data.hosts.get(host_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.instance_id}_host_{host_id}")},
            name=host.get("name") or f"Host {host_id}",
            manufacturer=MANUFACTURER,
            model="Managed Host (Wake-on-LAN)",
            via_device=(DOMAIN, coordinator.instance_id),
            configuration_url=coordinator.client.base_url,
        )

    @property
    def host(self) -> dict[str, Any] | None:
        return self.coordinator.data.hosts.get(self.host_id)

    @property
    def available(self) -> bool:
        return super().available and self.host is not None
