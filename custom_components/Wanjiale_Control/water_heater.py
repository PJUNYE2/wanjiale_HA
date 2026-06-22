"""万家乐 FW3/BA5/DW3 电热水器实体平台。"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity import WanjialeEntity
from .api import WanjialeApi, WanjialeWaterHeater
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MIN_TEMP = 30.0
MAX_TEMP = 75.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    api: WanjialeApi = entry_data["api"]
    coordinator = entry_data["coordinator"]

    devices: List[Any] = [
        WanjialeWaterHeaterEntity(dev, coordinator)
        for dev in api.devices
        if isinstance(dev, WanjialeWaterHeater)
    ]
    _LOGGER.info("创建 %d 个 FW3/BA5/DW3 热水器实体: %s", len(devices), [d.name for d in devices])
    async_add_entities(devices, True)


class WanjialeWaterHeaterEntity(WanjialeEntity, WaterHeaterEntity):
    """FW3/BA5/DW3 热水器调温实体。"""

    _attr_has_entity_name = False
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.ON_OFF
    )
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 1
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:water-boiler"

    def __init__(self, device: WanjialeWaterHeater, coordinator) -> None:
        super().__init__(device, coordinator)
        self._wh: WanjialeWaterHeater = device

    @property
    def name(self) -> str:
        return "调温"

    @property
    def is_on(self) -> Optional[bool]:
        return self._wh.is_power_on

    @property
    def current_temperature(self) -> Optional[float]:
        return self._wh.current_temperature

    @property
    def target_temperature(self) -> Optional[float]:
        return self._wh.target_temperature

    def turn_on(self, **kwargs: Any) -> None:
        self._wh.set_power(True)
        self.schedule_update_ha_state()
        self._request_refresh_soon()

    def turn_off(self, **kwargs: Any) -> None:
        self._wh.set_power(False)
        self.schedule_update_ha_state()
        self._request_refresh_soon()

    def set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            return
        self._wh.set_temperature(int(float(temp)))
        self.schedule_update_ha_state()
        self._request_refresh_soon()
