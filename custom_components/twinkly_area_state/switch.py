"""Maintenance switch remains available while all virtual lights are unloaded."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([MaintenanceSwitch(entry.entry_id,hass.data[DOMAIN][entry.entry_id]["maintenance"])])

class MaintenanceSwitch(SwitchEntity):
    _attr_has_entity_name=True
    _attr_should_poll=False
    _attr_name="Maintenance mode"
    _attr_icon="mdi:network-off"
    def __init__(self,entry_id,maintenance):
        self.maintenance=maintenance
        self._attr_unique_id=entry_id+"_maintenance_mode"
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,entry_id)},name="Twinkly Area Manager")
    @property
    def is_on(self):
        return self.maintenance.enabled
    async def async_turn_on(self,**kwargs):
        try:
            await self.maintenance.enable()
        finally:
            self.async_write_ha_state()
    async def async_turn_off(self,**kwargs):
        try:
            await self.maintenance.disable()
        finally:
            self.async_write_ha_state()
