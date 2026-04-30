"""Sensor platform for Oura HA Bridge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import OuraBundle
from .const import (
    ATTR_LATEST_DAYS,
    ATTR_RANGE,
    ATTR_REPORT,
    ATTR_SOURCE_DAY,
    DOMAIN,
    NAME,
)
from .coordinator import OuraHABridgeCoordinator


@dataclass(frozen=True, kw_only=True)
class OuraSensorDescription(SensorEntityDescription):
    """Describe an Oura sensor."""

    value_fn: Callable[[OuraBundle], Any]
    source_endpoint: str | None = None
    include_report: bool = False


def metric_value(key: str) -> Callable[[OuraBundle], Any]:
    """Return a function that extracts one metric."""

    return lambda data: data.metrics.get(key)


SENSORS: tuple[OuraSensorDescription, ...] = (
    OuraSensorDescription(
        key="summary",
        translation_key="summary",
        value_fn=metric_value("confidence"),
        include_report=True,
    ),
    OuraSensorDescription(
        key="latest_day",
        translation_key="latest_day",
        value_fn=metric_value("latest_day"),
    ),
    OuraSensorDescription(
        key="readiness_score",
        translation_key="readiness_score",
        native_unit_of_measurement="score",
        value_fn=metric_value("readiness_score"),
        source_endpoint="daily_readiness",
    ),
    OuraSensorDescription(
        key="sleep_score",
        translation_key="sleep_score",
        native_unit_of_measurement="score",
        value_fn=metric_value("sleep_score"),
        source_endpoint="daily_sleep",
    ),
    OuraSensorDescription(
        key="activity_score",
        translation_key="activity_score",
        native_unit_of_measurement="score",
        value_fn=metric_value("activity_score"),
        source_endpoint="daily_activity",
    ),
    OuraSensorDescription(
        key="steps",
        translation_key="steps",
        value_fn=metric_value("steps"),
        source_endpoint="daily_activity",
    ),
    OuraSensorDescription(
        key="active_calories",
        translation_key="active_calories",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        value_fn=metric_value("active_calories"),
        source_endpoint="daily_activity",
    ),
    OuraSensorDescription(
        key="inactivity_alerts",
        translation_key="inactivity_alerts",
        value_fn=metric_value("inactivity_alerts"),
        source_endpoint="daily_activity",
    ),
    OuraSensorDescription(
        key="stress_summary",
        translation_key="stress_summary",
        value_fn=metric_value("stress_summary"),
        source_endpoint="daily_stress",
    ),
    OuraSensorDescription(
        key="resilience_level",
        translation_key="resilience_level",
        value_fn=metric_value("resilience_level"),
        source_endpoint="daily_resilience",
    ),
    OuraSensorDescription(
        key="spo2_average",
        translation_key="spo2_average",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=metric_value("spo2_average"),
        source_endpoint="daily_spo2",
    ),
    OuraSensorDescription(
        key="breathing_disturbance_index",
        translation_key="breathing_disturbance_index",
        value_fn=metric_value("breathing_disturbance_index"),
        source_endpoint="daily_spo2",
    ),
    OuraSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=metric_value("battery_level"),
        source_endpoint="ring_battery_level",
    ),
    OuraSensorDescription(
        key="battery_timestamp",
        translation_key="battery_timestamp",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=metric_value("battery_timestamp"),
        source_endpoint="ring_battery_level",
    ),
    OuraSensorDescription(
        key="temperature_deviation",
        translation_key="temperature_deviation",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=metric_value("temperature_deviation"),
        source_endpoint="daily_readiness",
    ),
    OuraSensorDescription(
        key="sleep_duration",
        translation_key="sleep_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=metric_value("sleep_duration"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="time_in_bed",
        translation_key="time_in_bed",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=metric_value("time_in_bed"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="sleep_efficiency",
        translation_key="sleep_efficiency",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=metric_value("sleep_efficiency"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="average_hrv",
        translation_key="average_hrv",
        native_unit_of_measurement="ms",
        value_fn=metric_value("average_hrv"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="lowest_heart_rate",
        translation_key="lowest_heart_rate",
        native_unit_of_measurement="bpm",
        value_fn=metric_value("lowest_heart_rate"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="deep_sleep_duration",
        translation_key="deep_sleep_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=metric_value("deep_sleep_duration"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="rem_sleep_duration",
        translation_key="rem_sleep_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=metric_value("rem_sleep_duration"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="bedtime_start",
        translation_key="bedtime_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=metric_value("bedtime_start"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="bedtime_end",
        translation_key="bedtime_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=metric_value("bedtime_end"),
        source_endpoint="sleep",
    ),
    OuraSensorDescription(
        key="stress_high",
        translation_key="stress_high",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=metric_value("stress_high"),
        source_endpoint="daily_stress",
    ),
    OuraSensorDescription(
        key="recovery_high",
        translation_key="recovery_high",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=metric_value("recovery_high"),
        source_endpoint="daily_stress",
    ),
    OuraSensorDescription(
        key="synced_at",
        translation_key="synced_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=metric_value("synced_at"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Oura HA Bridge sensors."""

    coordinator: OuraHABridgeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        OuraSensor(coordinator, entry, description) for description in SENSORS
    )


class OuraSensor(CoordinatorEntity[OuraHABridgeCoordinator], SensorEntity):
    """Oura sensor entity."""

    entity_description: OuraSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OuraHABridgeCoordinator,
        entry: ConfigEntry,
        description: OuraSensorDescription,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_suggested_object_id = f"{DOMAIN}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Oura",
            model="Ring",
            name=entry.title or NAME,
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor state."""

        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return useful, non-secret attributes."""

        if not self.coordinator.data:
            return None
        attrs: dict[str, Any] = {}
        if self.entity_description.source_endpoint:
            attrs[ATTR_SOURCE_DAY] = self.coordinator.data.latest_days.get(
                self.entity_description.source_endpoint
            )
        if self.entity_description.include_report:
            attrs[ATTR_REPORT] = self.coordinator.data.report
            attrs[ATTR_LATEST_DAYS] = self.coordinator.data.latest_days
            attrs[ATTR_RANGE] = self.coordinator.data.raw.get("range")
        return attrs or None
