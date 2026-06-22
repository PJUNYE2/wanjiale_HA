"""万家乐 FW3/BA5/DW3 专用传感器平台。"""
from __future__ import annotations

from typing import Any, List, Optional

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity import WanjialeEntity
from .api import WanjialeApi, WanjialeWaterHeater
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: WanjialeApi = entry_data["api"]
    coordinator = entry_data["coordinator"]

    entities: List[Any] = []
    for dev in api.devices:
        if isinstance(dev, WanjialeWaterHeater):
            entities.append(WanjialeCurrentTemperatureSensor(dev, coordinator))
            entities.append(WanjialeTargetTemperatureSensor(dev, coordinator))
            entities.append(WanjialeHotWaterAmountSensor(dev, coordinator))

    async_add_entities(entities, True)


class WanjialeFw3Sensor(WanjialeEntity, SensorEntity):
    """FW3/BA5/DW3 传感器基类。"""

    _attr_has_entity_name = False

    def _as_int(self, key: str) -> Optional[int]:
        as_data = self._device.attributes.get("as", {})
        if not isinstance(as_data, dict) or key not in as_data:
            return None
        try:
            return int(float(as_data[key]))
        except (TypeError, ValueError):
            return None


class WanjialeCurrentTemperatureSensor(WanjialeFw3Sensor):
    """当前温度。"""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "°C"
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:thermometer"

    def __init__(self, device: WanjialeWaterHeater, coordinator) -> None:
        super().__init__(device, coordinator)
        self._attr_name = "当前温度"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id()}-current_temperature"

    @property
    def native_value(self):
        if self._device.current_temperature is not None:
            return self._device.current_temperature
        return self._as_int(self._device.DVID_CURRENT_TEMP)


class WanjialeTargetTemperatureSensor(WanjialeFw3Sensor):
    """设置温度。"""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "°C"
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:thermometer-lines"

    def __init__(self, device: WanjialeWaterHeater, coordinator) -> None:
        super().__init__(device, coordinator)
        self._attr_name = "设置温度"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id()}-target_temperature"

    @property
    def native_value(self):
        if self._device.target_temperature is not None:
            return self._device.target_temperature
        return self._as_int(self._device.DVID_TARGET_TEMP)


class WanjialeHotWaterAmountSensor(WanjialeFw3Sensor):
    """当前热水量。"""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:water-percent"

    def __init__(self, device: WanjialeWaterHeater, coordinator) -> None:
        super().__init__(device, coordinator)
        self._attr_name = "当前热水量"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id()}-hot_water_amount"

    @property
    def native_value(self):
        if self._device.hot_water_amount is not None:
            return self._device.hot_water_amount
        return self._as_int(self._device.DVID_HOT_WATER_AMOUNT)
