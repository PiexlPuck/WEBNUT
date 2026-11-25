"""Sensor platform for WebNUT."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ORCHESTRATOR_STATES, WAKE_STATES
from .coordinator import WebNutConfigEntry, WebNutCoordinator
from .entity import WebNutHostEntity, WebNutServerEntity, WebNutUpsEntity


@dataclass(frozen=True, kw_only=True)
class WebNutSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


UPS_SENSORS: tuple[WebNutSensorDescription, ...] = (
    WebNutSensorDescription(
        key="status",
        translation_key="ups_status",
        icon="mdi:power-plug-battery",
        value_fn=lambda u: u.get("status_label"),
    ),
    WebNutSensorDescription(
        key="status_flags",
        translation_key="ups_status_flags",
        icon="mdi:flag",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda u: u.get("status"),
    ),
    WebNutSensorDescription(
        key="battery_charge",
        translation_key="battery_charge",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda u: u.get("battery_charge"),
    ),
    WebNutSensorDescription(
        key="battery_runtime",
        translation_key="battery_runtime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda u: u.get("battery_runtime"),
    ),
    WebNutSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda u: u.get("battery_voltage"),
    ),
    WebNutSensorDescription(
        key="load",
        translation_key="ups_load",
        icon="mdi:gauge",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda u: u.get("ups_load"),
    ),
    WebNutSensorDescription(
        key="power",
        translation_key="ups_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda u: u.get("realpower_watts"),
    ),
    WebNutSensorDescription(
        key="input_voltage",
        translation_key="input_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda u: u.get("input_voltage"),
    ),
    WebNutSensorDescription(
        key="output_voltage",
        translation_key="output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda u: u.get("output_voltage"),
    ),
)

SERVER_SENSORS: tuple[WebNutSensorDescription, ...] = (
    WebNutSensorDescription(
        key="orchestrator_state",
        translation_key="orchestrator_state",
        device_class=SensorDeviceClass.ENUM,
        options=ORCHESTRATOR_STATES,
        icon="mdi:state-machine",
        value_fn=lambda d: str(d["orchestrator"]["state"]).lower(),
    ),
    WebNutSensorDescription(
        key="hosts_online",
        translation_key="hosts_online",
        icon="mdi:server-network",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("hosts_online"),
    ),
    WebNutSensorDescription(
        key="hosts_total",
        translation_key="hosts_total",
        icon="mdi:server",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("hosts_total"),
    ),
    WebNutSensorDescription(
        key="clients_connected",
        translation_key="clients_connected",
        icon="mdi:lan-connect",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("clients_connected"),
    ),
    WebNutSensorDescription(
        key="stability_remaining",
        translation_key="stability_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["orchestrator"].get("stability_remaining_sec"),
    ),
    WebNutSensorDescription(
        key="disconnect_elapsed",
        translation_key="disconnect_elapsed",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d["orchestrator"].get("disconnect_elapsed_sec"),
    ),
    WebNutSensorDescription(
        key="version",
        translation_key="version",
        icon="mdi:tag",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("version"),
    ),
)

HOST_SENSORS: tuple[WebNutSensorDescription, ...] = (
    WebNutSensorDescription(
        key="wake_state",
        translation_key="wake_state",
        device_class=SensorDeviceClass.ENUM,
        options=WAKE_STATES,
        icon="mdi:power-sleep",
        value_fn=lambda h: str(h.get("wake_state") or "IDLE").lower(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebNutConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(WebNutServerSensor(coordinator, d) for d in SERVER_SENSORS)

    known_ups: set[str] = set()
    known_hosts: set[int] = set()

    @callback
    def _add_new() -> None:
        new: list[SensorEntity] = []
        for ups_name in coordinator.data.ups:
            if ups_name not in known_ups:
                known_ups.add(ups_name)
                new.extend(WebNutUpsSensor(coordinator, ups_name, d) for d in UPS_SENSORS)
        for host_id in coordinator.data.hosts:
            if host_id not in known_hosts:
                known_hosts.add(host_id)
                new.extend(WebNutHostSensor(coordinator, host_id, d) for d in HOST_SENSORS)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WebNutServerSensor(WebNutServerEntity, SensorEntity):
    entity_description: WebNutSensorDescription

    def __init__(self, coordinator: WebNutCoordinator, description: WebNutSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data.raw)


class WebNutUpsSensor(WebNutUpsEntity, SensorEntity):
    entity_description: WebNutSensorDescription

    def __init__(
        self, coordinator: WebNutCoordinator, ups_name: str, description: WebNutSensorDescription
    ) -> None:
        super().__init__(coordinator, ups_name, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        ups = self.ups
        return self.entity_description.value_fn(ups) if ups else None


class WebNutHostSensor(WebNutHostEntity, SensorEntity):
    entity_description: WebNutSensorDescription

    def __init__(
        self, coordinator: WebNutCoordinator, host_id: int, description: WebNutSensorDescription
    ) -> None:
        super().__init__(coordinator, host_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        host = self.host
        return self.entity_description.value_fn(host) if host else None
