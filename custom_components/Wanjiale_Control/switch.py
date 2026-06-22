"""万家乐 FW3/BA5/DW3 专用开关平台。"""
from __future__ import annotations

from typing import Any, List

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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

    devices: List[Any] = [
        WanjialePowerSwitch(dev, coordinator)
        for dev in api.devices
        if isinstance(dev, WanjialeWaterHeater)
    ]
    async_add_entities(devices, True)


class WanjialePowerSwitch(WanjialeEntity, SwitchEntity):
    """FW3/BA5/DW3 电源开关。"""

    _attr_has_entity_name = False
    _attr_icon = "mdi:power"

    def __init__(self, device: WanjialeWaterHeater, coordinator) -> None:
        super().__init__(device, coordinator)
        self._wh = device
        self._attr_name = "电源"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id()}-power"

    @property
    def is_on(self) -> bool:
        return bool(self._wh.is_power_on)

    def turn_on(self, **kwargs: Any) -> None:
        self._wh.set_power(True)
        self.schedule_update_ha_state()
        self._request_refresh_soon()

    def turn_off(self, **kwargs: Any) -> None:
        self._wh.set_power(False)
        self.schedule_update_ha_state()
        self._request_refresh_soon()
