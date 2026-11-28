"""Maintenance switch remains available while all virtual lights are unloaded."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN

async def async_setup_entry(hass,entry,async_add_entities):
    ctx = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MaintenanceSwitch(entry.entry_id, ctx["maintenance"]),
                        VisitorSwitch(entry.entry_id, ctx["visitor"]),
                        VisitorFallbackSwitch(entry.entry_id, ctx["visitor"]),
                        VisitorMasterSwitch(entry.entry_id, ctx["visitor"])])

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


class VisitorSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Visitor colour match"
    _attr_icon = "mdi:palette"

    def __init__(self, entry_id, visitor):
        self.visitor = visitor
        self._attr_unique_id = entry_id + "_visitor_colour_match"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Twinkly Area Manager")

    @property
    def is_on(self):
        return self.visitor.enabled

    async def async_turn_on(self, **kwargs):
        self.visitor.set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.visitor.set_enabled(False)
        self.async_write_ha_state()


class VisitorFallbackSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Visitor button flourish"
    _attr_icon = "mdi:string-lights"

    def __init__(self, entry_id, visitor):
        self.visitor = visitor
        self._attr_unique_id = entry_id + "_visitor_button_flourish"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)}, name="Twinkly Area Manager")

    @property
    def is_on(self):
        return self.visitor.fallback_enabled

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        self.visitor.fallback_enabled = previous.state == "on" if previous and previous.state in ("on", "off") else True

    async def async_turn_on(self, **kwargs):
        self.visitor.fallback_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.visitor.fallback_enabled = False
        self.async_write_ha_state()


class VisitorMasterSwitch(VisitorFallbackSwitch):
    _attr_name = "Visitor master enable"
    _attr_icon = "mdi:power"

    def __init__(self, entry_id, visitor):
        super().__init__(entry_id, visitor)
        self._attr_unique_id = entry_id + "_visitor_master_enable"

    @property
    def is_on(self):
        return self.visitor.master_enabled

    async def async_added_to_hass(self):
        # Skip the fallback-specific restore hook.
        await super(VisitorFallbackSwitch, self).async_added_to_hass()
        previous = await self.async_get_last_state()
        self.visitor.master_enabled = previous.state == "on" if previous and previous.state in ("on", "off") else True

    async def async_turn_on(self, **kwargs):
        self.visitor.master_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.visitor.master_enabled = False
        self.visitor.generation += 1
        self.async_write_ha_state()
