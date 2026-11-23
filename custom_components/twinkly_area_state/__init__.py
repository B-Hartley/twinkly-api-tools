"""Twinkly Area named-state snapshots. No destructive API writes.

Only POST requests: Twinkly login and verify authentication.
All data capture endpoints are GET. Restore is deliberately a validation plan.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
from datetime import datetime, timezone

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.persistent_notification import async_create

from .const import DOMAIN, CONF_DEVICES_JSON
from .config_flow import validate_devices

_LOGGER = logging.getLogger(__name__)
ENDPOINTS = {
    "gestalt": "/xled/v1/gestalt",
    "summary": "/xled/v1/summary",
    "layout": "/xled/v1/led/layout/full",
    "group_config": "/xled/v1/led/movie/config",
    "mode": "/xled/v1/led/mode",
    "config": "/xled/v1/led/config",
}
SENSITIVE = {"authentication_token", "challenge", "challenge-response", "password", "token", "secret"}
STATE_NAME = vol.All(str, vol.Match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$"))
MAX_RESPONSE = 8 * 1024 * 1024


def redact(value):
    """Protect transient auth tokens from persisted snapshots."""
    if isinstance(value, dict):
        return {k: "<redacted>" if k.lower() in SENSITIVE else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def query(session, ip, endpoint, *, method="GET", payload=None, token=None):
    """Make a bounded local request with no redirects."""
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    url = f"http://{ip}{endpoint}"
    try:
        async with session.request(method, url, json=payload, headers=headers,
                                   allow_redirects=False, timeout=6) as resp:
            if resp.status != 200:
                return {"ok": False, "error": f"HTTP {resp.status}"}
            raw = await resp.content.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                return {"ok": False, "error": "Response too large"}
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("code") not in (None, 1000):
                return {"ok": False, "error": f"Device code {data.get('code')}"}
            return {"ok": True, "data": data}
    except (asyncio.TimeoutError, OSError, ValueError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def capture(session, device):
    ip = device["ip"]
    # Fast connectivity probe allows offline controllers to be reported once.
    probe = await query(session, ip, ENDPOINTS["gestalt"])
    if not probe["ok"]:
        return {"ip": ip, "name": device["name"], "status": "skipped",
                "reason": probe["error"], "responses": {}}
    challenge = base64.b64encode(os.urandom(32)).decode("ascii")
    login = await query(session, ip, "/xled/v1/login", method="POST", payload={"challenge": challenge})
    token = None
    auth_error = None
    if login["ok"] and isinstance(login.get("data"), dict):
        token = login["data"].get("authentication_token")
        answer = login["data"].get("challenge-response")
        if token and answer:
            verify = await query(session, ip, "/xled/v1/verify", method="POST",
                                 payload={"challenge-response": answer}, token=token)
            if not verify["ok"]:
                auth_error, token = verify["error"], None
        else:
            token = None
            auth_error = "Authentication response incomplete"
    else:
        auth_error = login.get("error", "Login failed")
    responses = {"gestalt": redact(probe)}
    for label, endpoint in ENDPOINTS.items():
        if label == "gestalt":
            continue
        if not token:
            responses[label] = {"ok": False, "error": auth_error or "Unauthenticated"}
            continue
        responses[label] = redact(await query(session, ip, endpoint, token=token))
    return {"ip": ip, "name": device["name"], "status": "captured",
            "authentication": "verified" if token else "failed",
            "responses": responses}


def device_summary(item):
    responses = item.get("responses", {})
    layout = responses.get("layout", {})
    data = layout.get("data") or {}
    coords = data.get("coordinates", []) if isinstance(data, dict) else []
    identity = responses.get("gestalt", {}).get("data") or {}
    grp = responses.get("group_config", {}).get("data") or {}
    sync = grp.get("sync", {}) if isinstance(grp, dict) else {}
    return {
        "name": item["name"], "ip": item["ip"], "status": item["status"],
        "led_count": identity.get("number_of_led"),
        "mac": identity.get("mac"),
        "layout_count": len(coords) if isinstance(coords, list) else 0,
        "layout_sha256": digest(coords) if coords else None,
        "role": sync.get("mode") if isinstance(sync, dict) else None,
        "group_id": (sync.get("master_id") or sync.get("slave_id")) if isinstance(sync, dict) else None,
    }


def restore_plan(source, live):
    """Only calculate differences: never post settings to a controller."""
    before = {d["ip"]: device_summary(d) for d in source["devices"]}
    after = {d["ip"]: device_summary(d) for d in live["devices"]}
    issues = []
    changes = []
    for ip, old in before.items():
        new = after.get(ip)
        if not new:
            issues.append(f"{old['name']}: missing in current capture")
            continue
        if new["status"] == "skipped":
            issues.append(f"{old['name']}: currently offline")
        if not old["layout_sha256"]:
            issues.append(f"{old['name']}: saved coordinate data unavailable")
        if old["mac"] and new["mac"] and old["mac"] != new["mac"]:
            issues.append(f"{old['name']}: controller identity changed")
        if old["led_count"] and new["led_count"] and old["led_count"] != new["led_count"]:
            issues.append(f"{old['name']}: LED count changed")
        if old["layout_sha256"] and new["layout_sha256"] and old["layout_sha256"] != new["layout_sha256"]:
            changes.append(f"{old['name']}: coordinates changed")
        if old["group_id"] != new["group_id"] or old["role"] != new["role"]:
            changes.append(f"{old['name']}: group membership/role changed")
    return {"source_name": source["name"], "controllers": list(before.values()),
            "current": list(after.values()), "issues": issues, "changes": changes,
            "restore_supported": False,
            "reason": "No verified Twinkly Area group creation or geometry restore API exists in this integration. No device writes performed."}


def window_plan(snapshot, left_name, right_name, gap_width=1.0):
    """Propose an equal-width, one-window-width-gap 2D placement, no writes."""
    names = {d["name"].casefold(): d for d in snapshot["devices"]}
    if left_name.casefold() == right_name.casefold():
        raise ValueError("Choose two different controllers")
    results = []
    for i, name in enumerate((left_name, right_name)):
        item = names.get(name.casefold())
        if not item:
            raise ValueError(f"No saved device named {name}")
        data = item.get("responses", {}).get("layout", {}).get("data") or {}
        coords = data.get("coordinates") or []
        if not coords or any(not isinstance(p, dict) or "x" not in p or "y" not in p for p in coords):
            raise ValueError(f"Missing X/Y coordinates for {name}")
        min_x, max_x = min(p["x"] for p in coords), max(p["x"] for p in coords)
        min_y, max_y = min(p["y"] for p in coords), max(p["y"] for p in coords)
        width, height = max_x - min_x, max_y - min_y
        if width <= 0 or height <= 0:
            raise ValueError(f"Degenerate mapping for {name}")
        # Equal horizontal and vertical scale for both windows. Each window maps
        # into a local 1x1 box; gap_width=1 means one full window-width gap.
        points = [{"x": round((p["x"] - min_x) / width + i * (1 + gap_width), 6),
                   "y": round((p["y"] - min_y) / height, 6),
                   "z_original": p.get("z")} for p in coords]
        results.append({"name": name, "ip": item["ip"], "led_count": len(points),
                        "suggested_coordinates": points})
    return {"description": "Concept-only 2D placement; no grouping or layout API writes",
            "gap_in_window_widths": gap_width, "windows": results}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register capture, audit, and explicitly non-destructive restore-plan actions."""
    devices = validate_devices(entry.data[CONF_DEVICES_JSON])
    store = Store(hass, 1, f"{DOMAIN}.states")
    lock = asyncio.Lock()
    session = async_get_clientsession(hass)

    async def collect(name):
        result = await asyncio.gather(*(capture(session, d) for d in devices))
        return {"name": name, "captured_utc": datetime.now(timezone.utc).isoformat(),
                "devices": result}

    async def save_state(call: ServiceCall):
        async with lock:
            state = await collect(call.data["name"])
            snapshots = await store.async_load() or {}
            snapshots[call.data["name"]] = state
            await store.async_save(snapshots)
            skipped = [d["name"] for d in state["devices"] if d["status"] == "skipped"]
            incomplete = [d["name"] for d in state["devices"] if not d.get("responses", {}).get("layout", {}).get("ok")]
            async_create(hass, "State saved as **" + call.data["name"] + "**. "
                         + f"Offline: {', '.join(skipped) or 'none'}. "
                         + f"Layouts missing: {', '.join(incomplete) or 'none'}. "
                         + "CAUTION: saved data is not yet a proven restorable Area group.",
                         title="Twinkly Area state capture", notification_id=f"{DOMAIN}_save")

    async def restore_state(call: ServiceCall):
        async with lock:
            snapshots = await store.async_load() or {}
            state = snapshots.get(call.data["name"])
            if not state:
                raise ValueError(f"No saved state named {call.data['name']}")
            current = await collect("live-read-only")
            plan = restore_plan(state, current)
            async_create(hass, f"**Restore plan for {state['name']}** — no commands sent. "
                         f"{len(plan['controllers'])} saved controllers; "
                         f"issues: {', '.join(plan['issues']) or 'none detected'}. "
                         + plan["reason"], title="Twinkly restore (dry run only)",
                         notification_id=f"{DOMAIN}_restore")

    async def office_plan(call: ServiceCall):
        async with lock:
            snapshots = await store.async_load() or {}
            state = snapshots.get(call.data["name"])
            if not state:
                raise ValueError(f"No saved state named {call.data['name']}")
            plan = window_plan(state, call.data["left"], call.data["right"])
            snapshots[f"{state['name']}_office_proposal"] = {
                "name": f"{state['name']}_office_proposal",
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "kind": "geometry_proposal_only", "plan": plan,
            }
            await store.async_save(snapshots)
            summary = ", ".join(f"{w['name']}: {w['led_count']} LEDs" for w in plan["windows"])
            async_create(hass, "Created a non-destructive equal-size, one-width-gap "
                         f"2D proposal for {summary}. Nothing was written to Twinkly. "
                         f"Proposed geometry saved as {state['name']}_office_proposal "
                         "in Home Assistant local storage. Group construction is not implemented.",
                         title="Twinkly office window test plan",
                         notification_id=f"{DOMAIN}_office")

    hass.services.async_register(DOMAIN, "save_state", save_state,
        schema=vol.Schema({vol.Required("name"): STATE_NAME}))
    hass.services.async_register(DOMAIN, "restore_state", restore_state,
        schema=vol.Schema({vol.Required("name"): STATE_NAME}))
    hass.services.async_register(DOMAIN, "office_window_plan", office_plan,
        schema=vol.Schema({vol.Required("name"): STATE_NAME,
                           vol.Required("left"): str, vol.Required("right"): str}))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"store": store}
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    for service in ("save_state", "restore_state", "office_window_plan"):
        hass.services.async_remove(DOMAIN, service)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
