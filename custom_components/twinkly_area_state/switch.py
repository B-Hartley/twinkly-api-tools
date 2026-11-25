"""Persistent mapping mode switch."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN


async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([MappingSwitch(entry,hass.data[DOMAIN][entry.entry_id]["mapping"])])


class MappingSwitch(SwitchEntity):
    _attr_has_entity_name=True
    _attr_name="Mapping mode"
    _attr_icon="mdi:map-marker-off"
    _attr_should_poll=False

    def __init__(self,entry,mapping):
        self.entry=entry
        self.mapping=mapping
        self._attr_unique_id=f"{entry.entry_id}_mapping_mode"
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,entry.entry_id)},name="Twinkly Area Manager",manufacturer="Community",model="Area capture and verification")

    @property
    def is_on(self):
        return self.mapping.enabled

    @property
    def extra_state_attributes(self):
        return {"suspended_native_entries":len(self.mapping.owned),"issues":self.mapping.issues}

    async def async_turn_on(self,**kwargs):
        try:
            await self.mapping.enable()
        finally:
            self.async_write_ha_state()

    async def async_turn_off(self,**kwargs):
        try:
            await self.mapping.disable()
        finally:
            self.async_write_ha_state()
        # Refresh group membership after app mapping, not stale topology.
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
