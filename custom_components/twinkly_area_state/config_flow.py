"""Initial setup: explicit, private LAN controller list as JSON."""
import json
import ipaddress

import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN, CONF_DEVICES_JSON


def validate_devices(raw: str) -> list[dict[str, str]]:
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise ValueError("Expected a nonempty JSON list")
    names = set()
    ips = set()
    result = []
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("Each controller must be an object")
        name, ip = row.get("name"), row.get("ip")
        if not isinstance(name, str) or not name.strip() or not isinstance(ip, str):
            raise ValueError("Each controller must have a name and IP")
        address = ipaddress.ip_address(ip)
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("Only private LAN IPv4 addresses are accepted")
        if name.casefold() in names or str(address) in ips:
            raise ValueError("Names and IP addresses must be unique")
        names.add(name.casefold())
        ips.add(str(address))
        result.append({"name": name.strip(), "ip": str(address)})
    return result


def configured_twinkly_devices(entries):
    """Reuse HA's native Twinkly discovery/config entries; never scan LAN."""
    devices = []
    seen = set()
    for entry in entries:
        data = getattr(entry, "data", {}) or {}
        ip = data.get("host") or data.get("ip_address") or data.get("ip")
        if not isinstance(ip, str):
            continue
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (address.version != 4 or not address.is_private
                or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_unspecified):
            continue
        ip = str(address)
        if ip in seen:
            continue
        name = str(getattr(entry, "title", "") or f"Twinkly {ip}")
        devices.append({"name": name, "ip": ip})
        seen.add(ip)
    # Native entries can occasionally have duplicated titles.
    for index, device in enumerate(devices):
        if any(other["name"].casefold() == device["name"].casefold()
               for other in devices[:index]):
            device["name"] += " " + device["ip"]
    return devices


class TwinklyAreaStateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure controller names/IPs without probing or changing lights."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input:
            try:
                devices = validate_devices(user_input[CONF_DEVICES_JSON])
            except (ValueError, json.JSONDecodeError):
                errors["base"] = "invalid_devices"
            else:
                return self.async_create_entry(title="Twinkly Area States", data={
                    CONF_DEVICES_JSON: json.dumps(devices)
                })
        native = configured_twinkly_devices(self.hass.config_entries.async_entries("twinkly"))
        sample = json.dumps(native) if native else (
            '[{"name":"Office Left","ip":"192.168.21.90"},'
            '{"name":"Office Right","ip":"192.168.21.93"}]')
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_DEVICES_JSON, default=sample): str
        }), errors=errors)
