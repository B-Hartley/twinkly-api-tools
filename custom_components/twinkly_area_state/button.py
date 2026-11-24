"""Simple dashboard buttons for safe Twinkly Area snapshots and verification."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import ServiceCall
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


ACTIONS = (
    ("capture_groups", "Capture groups", "mdi:lightbulb-group"),
    ("capture_3d_maps", "Capture 3D maps", "mdi:vector-polyline"),
    ("verify_archives", "Verify maps and groups", "mdi:shield-check"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    actions = hass.data[DOMAIN][entry.entry_id]["actions"]
    async_add_entities([
        TwinklyAreaButton(entry.entry_id, name, title, icon, actions[name])
        for name, title, icon in ACTIONS
    ])


class TwinklyAreaButton(ButtonEntity):
    """Action buttons; capture/verify only, never group or ungroup."""

    _attr_has_entity_name = True

    def __init__(self, entry_id, action, title, icon, callback):
        self._attr_unique_id = f"{entry_id}_{action}"
        self._attr_name = title
        self._attr_icon = icon
        self._callback = callback
        self._action = action
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Twinkly Area Manager",
            manufacturer="Community",
            model="Area capture and verification",
        )

    async def async_press(self):
        await self._callback(ServiceCall(self.hass, DOMAIN, self._action, {}))
