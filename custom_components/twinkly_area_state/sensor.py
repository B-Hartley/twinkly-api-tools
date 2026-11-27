"""Event-driven readiness for phone dashboards and physical show buttons."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ChristmasShowStatus(entry.entry_id, hass.data[DOMAIN][entry.entry_id]["show"])])


class ChristmasShowStatus(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Christmas show status"
    _attr_icon = "mdi:movie-open-play"
    _attr_should_poll = False

    def __init__(self, entry_id, show):
        self.show = show
        self.entity_id = "sensor.twinkly_christmas_show_status"
        self._attr_unique_id = f"{entry_id}_christmas_show_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)}, name="Twinkly Area Manager",
            manufacturer="Community", model="Area capture and verification")

    async def async_added_to_hass(self):
        self.async_on_remove(self.show.add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.show.status

    @property
    def extra_state_attributes(self):
        prepared = self.show.prepared
        return {
            "programme": self.show.kind,
            "variant": self.show.variant,
            "ready": self.show.status == "ready" and prepared is not None,
            "audio_cleanup_pending": self.show._pending_audio_cleanup is not None,
            "soundtrack": prepared["plan"]["soundtrack"]["file"] if prepared else None,
            "ready_timeout_minutes": 15,
            "last_error": self.show.last_error,
            "house_light_warnings": list(self.show.house_lights.warnings) if self.show.house_lights else [],
        }
