"""Visitor announcement destination."""
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN
from .visitor import DEFAULT_SPEAKER, SPEAKERS, DEFAULT_COOLDOWN, DEFAULT_START, DEFAULT_END, MASTER_WINDOWS


async def async_setup_entry(hass, entry, async_add_entities):
    ctx = hass.data[DOMAIN][entry.entry_id]
    visitor = ctx["visitor"]
    times = [f"{h:02d}:{m:02d}" for h in range(24) for m in range(0, 60, 15)]
    async_add_entities([VisitorSpeaker(entry.entry_id, visitor),
        VisitorSetting(entry.entry_id, visitor, "cooldown", "Visitor cooldown minutes",
                       [str(m) for m in range(61)], str(DEFAULT_COOLDOWN)),
        VisitorSetting(entry.entry_id, visitor, "start_time", "Visitor start time", times, DEFAULT_START),
        VisitorSetting(entry.entry_id, visitor, "end_time", "Visitor end time", times, DEFAULT_END),
        *[VisitorSetting(entry.entry_id, visitor, key, "Visitor " + key.replace("_", " "),
                         times, value) for key, value in MASTER_WINDOWS.items()]])


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


class VisitorSetting(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:clock-outline"

    def __init__(self, entry_id, visitor, key, name, options, default):
        self.visitor, self.key, self.default = visitor, key, default
        self._attr_name = name
        self._attr_options = options
        self._attr_unique_id = entry_id + "_visitor_" + key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)}, name="Twinkly Area Manager")

    @property
    def current_option(self):
        return str(getattr(self.visitor, self.key))

    def _set(self, option):
        setattr(self.visitor, self.key, int(option) if self.key == "cooldown" else option)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        self._set(previous.state if previous and previous.state in self.options else self.default)

    async def async_select_option(self, option):
        if option not in self.options:
            raise ValueError("Invalid visitor setting")
        self._set(option)
        self.async_write_ha_state()
