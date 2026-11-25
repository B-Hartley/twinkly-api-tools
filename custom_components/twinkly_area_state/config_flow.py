"""Initial setup: explicit, private LAN controller list as JSON."""
import json
import ipaddress

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

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
    """Select existing native Twinkly controllers or configure LAN IPs manually."""
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return TwinklyCloudOptionsFlow()

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        devices = configured_twinkly_devices(
            self.hass.config_entries.async_entries("twinkly"))
        if devices:
            return await self.async_step_native(user_input)
        return await self.async_step_manual(user_input)

    async def async_step_native(self, user_input=None):
        devices = configured_twinkly_devices(
            self.hass.config_entries.async_entries("twinkly"))
        if not devices:
            return await self.async_step_manual()
        errors = {}
        if user_input is not None:
            if user_input.get("manual_setup"):
                return await self.async_step_manual()
            selected = user_input.get("selected_ips", [])
            if isinstance(selected, list) and selected:
                picked = [d for d in devices if d["ip"] in selected]
                if picked and len(picked) == len(set(selected)):
                    return self.async_create_entry(
                        title="Twinkly Area Manager",
                        data={CONF_DEVICES_JSON: json.dumps(picked)})
            errors["base"] = "invalid_devices"
        options = [{"label": d["name"] + " (" + d["ip"] + ")",
                    "value": d["ip"]} for d in devices]
        return self.async_show_form(
            step_id="native",
            data_schema=vol.Schema({
                vol.Required("selected_ips", default=[d["ip"] for d in devices]):
                    selector.SelectSelector(selector.SelectSelectorConfig(
                        options=options, multiple=True,
                        mode=selector.SelectSelectorMode.LIST)),
                vol.Optional("manual_setup", default=False): bool,
            }),
            errors=errors,
        )

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                devices = validate_devices(user_input[CONF_DEVICES_JSON])
            except (ValueError, json.JSONDecodeError, KeyError):
                errors["base"] = "invalid_devices"
            else:
                return self.async_create_entry(
                    title="Twinkly Area Manager",
                    data={CONF_DEVICES_JSON: json.dumps(devices)})
        example = '[{"name":"Office Left","ip":"192.168.21.90"},{"name":"Office Right","ip":"192.168.21.93"}]'
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICES_JSON, default=example): str,
            }),
            errors=errors,
        )


class TwinklyCloudOptionsFlow(config_entries.OptionsFlow):
    """Optional cloud authentication without storing the account password."""
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["controllers", "cloud"])

    async def async_step_controllers(self, user_input=None):
        errors = {}
        current = validate_devices(self.config_entry.data[CONF_DEVICES_JSON])
        if user_input is not None:
            try:
                devices = validate_devices(user_input[CONF_DEVICES_JSON])
                # Explicit replacement, but require acknowledgement for removals.
                removed = {d["ip"] for d in current} - {d["ip"] for d in devices}
                if removed and not user_input.get("confirm_removal"):
                    errors["base"] = "confirm_removal"
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data={**self.config_entry.data,
                        CONF_DEVICES_JSON: json.dumps(devices)})
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    return self.async_create_entry(title="", data=dict(self.config_entry.options))
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_devices"
        return self.async_show_form(step_id="controllers", data_schema=vol.Schema({
            vol.Required(CONF_DEVICES_JSON, default=json.dumps(current, indent=2)):
                selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional("confirm_removal", default=False): bool,
        }), errors=errors)

    async def async_step_cloud(self,user_input=None):
        from .cloud_session import CloudSession
        from homeassistant.exceptions import HomeAssistantError
        cloud=self.hass.data.get(DOMAIN,{}).get(self.config_entry.entry_id,{}).get("cloud") or CloudSession(self.hass,self.config_entry.entry_id)
        errors={}
        if user_input is not None:
            try:
                if user_input.get("forget"):
                    await cloud.forget()
                else:
                    if not user_input.get("username") or not user_input.get("password"):
                        raise HomeAssistantError("Both email and password are required")
                    await cloud.login(user_input["username"],user_input["password"])
                return self.async_create_entry(title="",data=dict(self.config_entry.options))
            except (HomeAssistantError,KeyError):
                errors["base"]="cloud_auth_failed"
        return self.async_show_form(step_id="cloud",data_schema=vol.Schema({
            vol.Optional("username"):str,
            vol.Optional("password"):selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional("forget",default=False):bool,
        }),errors=errors)
