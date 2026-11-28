"""Visitor announcement destination."""
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN
from .visitor import DEFAULT_SPEAKER, SPEAKERS


async def async_setup_entry(hass, entry, async_add_entities):
    ctx = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VisitorSpeaker(entry.entry_id, ctx["visitor"])])


class VisitorSpeaker(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Visitor announcement speaker"
    _attr_icon = "mdi:speaker"
    _attr_options = list(SPEAKERS)

    def __init__(self, entry_id, visitor):
        self.visitor = visitor
        self._attr_unique_id = entry_id + "_visitor_announcement_speaker"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)}, name="Twinkly Area Manager")

    @property
    def current_option(self):
        return self.visitor.speaker

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        self.visitor.speaker = (
            previous.state if previous and previous.state in SPEAKERS
            else DEFAULT_SPEAKER)

    async def async_select_option(self, option):
        if option not in SPEAKERS:
            raise ValueError("Unknown visitor announcement speaker")
        self.visitor.speaker = option
        self.async_write_ha_state()
