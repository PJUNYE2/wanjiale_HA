"""万家乐 FW3/BA5/DW3 调温 Number 实体。"""
from __future__ import annotations

import logging
from typing import Any, List

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity import WanjialeEntity
from .api import WanjialeApi, WanjialeWaterHeater
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: WanjialeApi = entry_data["api"]
    coordinator = entry_data["coordinator"]

    entities: List[Any] = [
        WanjialeTargetTemperatureNumber(dev, coordinator)
        for dev in api.devices
        if isinstance(dev, WanjialeWaterHeater)
    ]
    _LOGGER.info("创建 %d 个 FW3/BA5/DW3 调温实体", len(entities))
    async_add_entities(entities, True)


class WanjialeTargetTemperatureNumber(WanjialeEntity, NumberEntity):
    """FW3/BA5/DW3 设置温度。"""

    _attr_has_entity_name = False
    _attr_name = "调温"
    _attr_icon = "mdi:water-boiler"
    _attr_native_min_value = 30
    _attr_native_max_value = 75
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, device: WanjialeWaterHeater, coordinator) -> None:
        super().__init__(device, coordinator)
        self._wh = device

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id()}-target_temperature_number"

    @property
    def native_value(self):
        return self._wh.target_temperature

    def set_native_value(self, value: float) -> None:
        self._wh.set_temperature(int(float(value)))
        self.schedule_update_ha_state()
        self._request_refresh_soon()
