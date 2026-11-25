"""Twinkly Area named-state snapshots. No destructive API writes.

Capture endpoints are read-only apart from authentication. Virtual lights explicitly
write playback mode and HTTP real-time frames; never change maps or groups.
"""
from __future__ import annotations

import asyncio
import aiohttp
import base64
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
from pathlib import Path
from datetime import datetime, timezone

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.persistent_notification import async_create

from .const import DOMAIN, CONF_DEVICES_JSON
from .config_flow import validate_devices, configured_twinkly_devices
from .inventory import merge_controller_inventory

_LOGGER = logging.getLogger(__name__)
ENDPOINTS = {
    "gestalt": "/xled/v1/gestalt",
    "summary": "/xled/v1/summary",
    "layout": "/xled/v1/led/layout/full",
    "group_config": "/xled/v1/led/movie/config",
    "mode": "/xled/v1/led/mode",
    "config": "/xled/v1/led/config",
    "group": "/xled/v1/application/group",
    "movies": "/xled/v1/movies",
    "playlist": "/xled/v1/playlist",
    "current_movie": "/xled/v1/movies/current",
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


def parse_twinkly_json(raw):
    """Parse one JSON object; tolerate only the known extraneous connection header.

    Some Twinkly firmware appends an HTTP 'Connection: close' header to the
    response body. Never truncate incomplete JSON or accept arbitrary garbage.
    """
    body = raw.decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError as original:
        try:
            data, end = json.JSONDecoder().raw_decode(body.lstrip())
        except json.JSONDecodeError:
            raise ValueError(
                f"Malformed device JSON (bytes={len(raw)}, "
                f"pos={original.pos}, reason={original.msg})") from None
        suffix = body.lstrip()[end:].strip()
        if suffix.lower() != "connection: close":
            raise ValueError(
                f"Malformed device JSON (bytes={len(raw)}, "
                f"pos={original.pos}, trailing_data={bool(suffix)}, "
                "unrecognised_suffix)") from None
        _LOGGER.debug("Accepted Twinkly known Connection-close body suffix")
        return data


async def query(session, ip, endpoint, *, method="GET", payload=None, token=None, debug_capture=None):
    """Read the complete bounded response; retry incomplete GET JSON after cooldown.

    aiohttp StreamReader.read(n) can return fewer than n bytes even when more
    data will arrive. Accumulate chunks until EOF rather than parsing the first.
    Never retry authentication POSTs or other writes.
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    url = f"http://{ip}{endpoint}"
    attempts = 3 if method == "GET" else 1
    for attempt in range(1, attempts + 1):
        if debug_capture is not None:
            debug_capture.clear()
            debug_capture["attempt"] = attempt
        try:
            async with session.request(
                method, url, json=payload, headers=headers,
                allow_redirects=False, timeout=15,
            ) as resp:
                if debug_capture is not None:
                    debug_capture["status"] = resp.status
                    debug_capture["content_type"] = resp.headers.get("Content-Type", "")[:100]
                if resp.status != 200:
                    return {"ok": False, "error": f"HTTP {resp.status}"}
                chunks = []
                total = 0
                while True:
                    chunk = await resp.content.read(min(64 * 1024, MAX_RESPONSE + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_RESPONSE:
                        return {"ok": False, "error": "Response too large"}
                raw = b"".join(chunks)
                if debug_capture is not None:
                    debug_capture["body"] = raw
                try:
                    data = parse_twinkly_json(raw)
                except ValueError as exc:
                    # A successful HTTP response with unfinished JSON may be
                    # an interrupted stream. Retry fresh, never splice fragments.
                    if method == "GET" and attempt < attempts:
                        _LOGGER.debug(
                            "Twinkly %s GET returned invalid JSON on attempt %s/%s; "
                            "retrying after cooldown: %s", endpoint, attempt, attempts, exc
                        )
                        await asyncio.sleep(float(attempt))
                        continue
                    return {"ok": False, "error": (
                        f"Incomplete or malformed controller JSON after {attempt} "
                        f"attempt(s): {exc}"
                    )}
                if isinstance(data, dict) and data.get("code") not in (None, 1000):
                    return {"ok": False, "error": f"Device code {data.get('code')}"}
                return {"ok": True, "data": data}
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "error": "Controller read failed after retries"}


async def capture(session, device, *, layout_dump=None):
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
        responses[label] = redact(await query(
            session, ip, endpoint, token=token,
            debug_capture=layout_dump if label == "layout" else None))
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


def _write_layout_diagnostic(root, device, snapshot, response):
    """Save only layout GET body/status. No login or authentication response."""
    destination = Path(root)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_name = re.sub(r"[^a-z0-9_-]+", "-", device["name"].lower()).strip("-")[:40]
    base = stamp + "-" + (safe_name or "controller")
    raw = snapshot.get("body", b"")
    max_dump = 1024 * 1024
    if not isinstance(raw, bytes):
        raw = b""
    body = raw[:max_dump]
    details = {
        "controller": device["name"],
        "ip": device["ip"],
        "endpoint": ENDPOINTS["layout"],
        "http_status": snapshot.get("status"),
        "content_type": snapshot.get("content_type"),
        "response_bytes": len(raw),
        "truncated": len(raw) > max_dump,
        "capture_error": response.get("responses", {}).get("layout", {}).get("error"),
        "authentication": response.get("authentication"),
        "note": "Contains raw LED mapping coordinates. No login response, tokens or HTTP auth headers.",
    }
    for suffix, data in (
        ("-layout-response.txt", body),
        ("-layout-metadata.json", json.dumps(details, indent=2).encode("utf-8")),
    ):
        path = destination / (base + suffix)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return base


def _write_capture_diagnostic(root, action, state, layout_dumps):
    """Private per-run capture JSON and exact layout bodies, even on validation failure.

    Never persist session tokens, authentication headers or login/verify replies.
    A layout body is retained verbatim because malformed JSON is the fault under
    investigation; this data contains private LED geometry.
    """
    destination = Path(root)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_action = re.sub(r"[^a-z0-9_-]+", "-", action.lower()).strip("-")
    basename = stamp + "-" + safe_action
    devices_out = []
    for i, item in enumerate(state["devices"]):
        snap = layout_dumps[i]
        raw = snap.get("body")
        layout_file = None
        if isinstance(raw, bytes):
            layout_file = f"{basename}-device-{i + 1}-layout.txt"
            fd = os.open(destination / layout_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
        devices_out.append({
            "name": item["name"],
            "ip": item["ip"],
            "status": item["status"],
            "authentication": item.get("authentication"),
            "layout_http_status": snap.get("status"),
            "layout_content_type": snap.get("content_type"),
            "layout_response_bytes": len(raw) if isinstance(raw, bytes) else None,
            "raw_layout_file": layout_file,
            "responses": redact(item.get("responses", {})),
            "reason": item.get("reason"),
        })
    report = {
        "action": action,
        "captured_utc": state["captured_utc"],
        "warning": "PRIVATE: contains controller addresses, identities and LED coordinates. Do not commit to GitHub.",
        "devices": devices_out,
    }
    path = destination / (basename + ".json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return path.name


def _geometry_matches(a, b, tolerance=0.0003):
    """Accept only minor Twinkly floating-point round-trip differences."""
    if (not isinstance(a, dict) or not isinstance(b, dict)
            or a.get("source") != b.get("source")
            or a.get("synthesized") != b.get("synthesized")):
        return False
    left, right = a.get("coordinates"), b.get("coordinates")
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return False
    for one, two in zip(left, right):
        if not isinstance(one, dict) or not isinstance(two, dict):
            return False
        for axis in ("x", "y", "z"):
            a_value, b_value = one.get(axis), two.get(axis)
            if type(a_value) not in (float, int) or type(b_value) not in (float, int):
                return False
            if (not math.isfinite(a_value) or not math.isfinite(b_value)
                    or abs(a_value - b_value) > tolerance):
                return False
    return True


def _validated_controller(item):
    """Validate hardware identity, local role and LED map from authenticated GETs."""
    if item.get("status") != "captured":
        raise ValueError(item.get("name", "Controller") + ": offline")
    replies = item.get("responses", {})
    for key in ("gestalt", "layout", "group"):
        response = replies.get(key, {})
        if not response.get("ok"):
            detail = response.get("error", "not captured")
            raise ValueError(item["name"] + ": " + key + " read failed (" +
                             str(detail)[:180] + "); auth=" +
                             str(item.get("authentication", "unknown")))
    identity = replies["gestalt"]["data"]
    layout = replies["layout"]["data"]
    group = replies["group"]["data"].get("group")
    if not isinstance(identity, dict) or not isinstance(layout, dict) or not isinstance(group, dict):
        raise ValueError(item["name"] + ": invalid device reply")
    led_count = identity.get("number_of_led")
    address = str(identity.get("mac", "")).lower().replace(":", "")
    coords = layout.get("coordinates")
    if (not re.fullmatch(r"[0-9a-f]{12}", address)
            or type(led_count) is not int or led_count <= 0
            or not isinstance(coords, list) or len(coords) != led_count
            or layout.get("source") not in ("2d", "3d")):
        raise ValueError(item["name"] + ": invalid MAC / mapping / LED count")
    if any(not isinstance(p, dict) or any(
            type(p.get(axis)) not in (float, int)
            for axis in ("x", "y", "z")) for p in coords):
        raise ValueError(item["name"] + ": coordinates malformed")
    return address, led_count, layout, group


def _capture_local_groups(state):
    """Local grouped maps only; Twinkly cloud Area must be validated separately."""
    members = {}
    for item in state["devices"]:
        if item.get("status") != "captured":
            continue
        mac, count, layout, group = _validated_controller(item)
        if group.get("mode") not in ("master", "slave"):
            continue
        if layout.get("source") != "2d":
            raise ValueError(item["name"] + ": grouped but not 2D")
        uid = str(group.get("uid", "")).lower()
        if not re.fullmatch(r"[0-9a-f-]{36}", uid):
            raise ValueError(item["name"] + ": group UID missing")
        members.setdefault(uid, []).append({
            "name": item["name"], "ip": item["ip"], "mac": mac,
            "led_count": count, "layout": layout, "role": group["mode"],
            "offset": group.get("offset"), "size": group.get("size"),
            "movies": item["responses"].get("movies"),
            "playlist": item["responses"].get("playlist"),
        })
    if not members:
        raise ValueError("No live local groups found")
    records = {}
    for uid, grouped in members.items():
        if len(grouped) < 2 or sum(x["role"] == "master" for x in grouped) != 1:
            raise ValueError("Group " + uid + ": expected one master and at least one slave")
        grouped.sort(key=lambda x: 0 if x["role"] == "master" else 1)
        if grouped[0]["size"] != grouped[1]["size"] or grouped[0]["size"] != sum(
                x["led_count"] for x in grouped):
            raise ValueError("Group " + uid + ": inconsistent LED count")
        # Twinkly omits the master's offset when it starts at LED 0.
        # Only normalise that documented role-specific omission; missing or
        # malformed slave offsets must still fail rather than be guessed.
        for participant in grouped:
            if participant["role"] == "master" and participant["offset"] is None:
                participant["offset"] = 0
            if type(participant["offset"]) is not int or participant["offset"] < 0:
                raise ValueError(
                    "Group " + uid + ": missing or invalid offset for " + participant["name"]
                )
        ordered = sorted(grouped, key=lambda x: x["offset"])
        next_offset = 0
        for participant in ordered:
            if participant["offset"] != next_offset:
                raise ValueError("Group " + uid + ": inconsistent offsets")
            next_offset += participant["led_count"]
        records[uid] = {"members": grouped, "led_count": grouped[0]["size"],
                        "source": "local-group-only",
                        "cloud_area_verified": False}
    return records


def _capture_live_3d(state):
    """Capture only original standalone 3D maps; never archive grouped mappings."""
    # Check membership first so an incomplete group layout cannot obscure the
    # actionable "ungroup first" message.
    grouped = []
    for item in state["devices"]:
        role = (item.get("responses", {}).get("group", {}).get("data") or {}).get("group", {}).get("mode")
        if role in ("master", "slave"):
            grouped.append(item["name"])
    if grouped:
        raise ValueError(
            "Initial 3D map capture requires ungrouped standalone controllers. "
            "Currently grouped: " + ", ".join(grouped) +
            ". First preserve your existing group backup; then ungroup in the "
            "Twinkly app and retry Capture 3D maps. No maps were saved."
        )
    records = {}
    for item in state["devices"]:
        if item.get("status") != "captured":
            continue
        mac, count, layout, group = _validated_controller(item)
        if layout["source"] != "3d" or group.get("mode") != "none":
            continue
        records[mac] = {"name": item["name"], "ip_at_capture": item["ip"],
                        "led_count": count, "layout": layout,
                        "sha256": digest(layout),
                        "source": "live-standalone-3d"}
    if not records:
        raise ValueError(
            "No standalone 3D maps found. Initial 3D mapping requires each "
            "controller to be ungrouped and mapped in 3D in the Twinkly app."
        )
    return records


def _verify_archives(state, archives):
    """Compare live state with saved mapping for whichever state is currently active."""
    current = {}
    notes = []
    for item in state["devices"]:
        try:
            mac, count, layout, group = _validated_controller(item)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            notes.append(str(exc))
            continue
        if mac in current:
            notes.append("Duplicate controller identity: " + item["name"])
            continue
        current[mac] = {"count": count, "layout": layout, "group": group}
    saved_3d = archives.get("maps_3d", {})
    for mac, ref in saved_3d.items():
        observed = current.get(mac)
        if not observed:
            notes.append("Missing or unavailable controller " + ref.get("name", mac[-4:]))
        elif observed["group"].get("mode") == "none":
            if observed["layout"]["source"] != "3d" or not _geometry_matches(observed["layout"], ref["layout"]):
                notes.append("3D reference differs for " + ref.get("name", mac[-4:]))
    saved_groups = archives.get("groups", {})
    for uid, group in saved_groups.items():
        live_members = [(m, current.get(m["mac"])) for m in group["members"]]
        if not all(observed is not None for _, observed in live_members):
            notes.append("Group " + uid[:8] + ": member missing")
            continue
        # Saved UUID identifies the historical group. New cloud groups receive
        # new UIDs, so compare geometry only if the live group still has that UID.
        if all(observed["group"].get("mode") in ("master", "slave")
               for _, observed in live_members):
            for member, observed in live_members:
                if observed["layout"]["source"] != "2d":
                    notes.append("Group member not 2D: " + member["name"])
                elif not _geometry_matches(observed["layout"], member["layout"]):
                    notes.append("Saved group layout differs: " + member["name"])
    return notes


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Register capture, audit, and explicitly non-destructive restore-plan actions."""
    devices = validate_devices(entry.data[CONF_DEVICES_JSON])
    native = configured_twinkly_devices(hass.config_entries.async_entries("twinkly"))
    devices = merge_controller_inventory(devices, native)
    store = Store(hass, 1, f"{DOMAIN}.states")
    lock = asyncio.Lock()
    session = async_get_clientsession(hass)

    async def collect(name):
        # Capture sequentially: the device's local API tokens can be affected by
        # other active clients; avoid overlapping session setup unnecessarily.
        result = []
        layout_dumps = []
        for device in devices:
            raw = {}
            result.append(await capture(session, device, layout_dump=raw))
            layout_dumps.append(raw)
        state = {"name": name, "captured_utc": datetime.now(timezone.utc).isoformat(),
                 "devices": result}
        if name in ("capture-groups-local", "capture-3d-local"):
            # Write BEFORE validation so malformed responses survive an exception.
            filename = await hass.async_add_executor_job(
                _write_capture_diagnostic,
                hass.config.path("twinkly_area_diagnostics"), name, state, layout_dumps)
            async_create(
                hass, "Diagnostic JSON saved to **/config/twinkly_area_diagnostics/"
                + filename + "**. Exact layout response bodies are in adjacent "
                "device-layout.txt files when returned. These files are private "
                "and may contain controller identities and LED geometry.",
                title="Twinkly capture diagnostics saved",
                notification_id=f"{DOMAIN}_{name}_diagnostics",
            )
        return state

    async def capture_layout_diagnostics(call: ServiceCall):
        """Explicit opt-in raw layout diagnostics, never log secrets or responses."""
        async with lock:
            directory = hass.config.path("twinkly_area_diagnostics")
            completed = []
            for device in devices:
                raw = {}
                result = await capture(session, device, layout_dump=raw)
                basename = await hass.async_add_executor_job(
                    _write_layout_diagnostic, directory, device, raw, result)
                completed.append(device["name"] + " (" + basename + ")")
            async_create(
                hass,
                "Captured exact local layout response body and status for: "
                + ", ".join(completed)
                + ". Files: **/config/twinkly_area_diagnostics/**. "
                "Review locally; contents include private LED geometry. "
                "No controller settings changed.",
                title="Twinkly layout diagnostics saved",
                notification_id=f"{DOMAIN}_layout_debug",
            )

    async def capture_groups(call: ServiceCall):
        async with lock:
            live = await collect("capture-groups-local")
            records = _capture_local_groups(live)
            data = await store.async_load() or {}
            archives = data.setdefault("_area_manager", {"maps_3d": {}, "groups": {}})
            saved = archives.setdefault("groups", {})
            for uid, record in records.items():
                if uid in saved and digest(saved[uid]) != digest(record):
                    raise ValueError("Existing captured group " + uid + " differs; refusing overwrite")
                saved.setdefault(uid, record)
            await store.async_save(data)
            ctx = hass.data[DOMAIN][entry.entry_id]
            for group in ctx.get("virtual_groups", []):
                group.load_maps(data)
            for bush in ctx.get("combined_bushes", []):
                bush.load_maps(data)
            for group in ctx.get("virtual_groups", []):
                group.notify()
            async_create(hass, f"Captured {len(records)} group(s), with LED layouts and movie "
                         "catalogues, from the local controllers. Cloud Area identity is "
                         "NOT verified by this HA action. Existing captures were not overwritten.",
                         title="Twinkly groups captured",
                         notification_id=f"{DOMAIN}_capture_group")

    async def capture_3d_maps(call: ServiceCall):
        async with lock:
            live = await collect("capture-3d-local")
            records = _capture_live_3d(live)
            data = await store.async_load() or {}
            archives = data.setdefault("_area_manager", {"maps_3d": {}, "groups": {}})
            saved = archives.setdefault("maps_3d", {})
            for mac, record in records.items():
                if mac in saved and saved[mac]["sha256"] != record["sha256"]:
                    raise ValueError("Different 3D reference already saved for " + mac[-4:])
                saved.setdefault(mac, record)
            await store.async_save(data)
            ctx = hass.data[DOMAIN][entry.entry_id]
            for group in ctx.get("virtual_groups", []):
                group.load_maps(data)
            for bush in ctx.get("combined_bushes", []):
                bush.load_maps(data)
            for group in ctx.get("virtual_groups", []):
                group.notify()
            async_create(hass, f"Captured {len(records)} standalone 3D mapping(s). "
                         "Controllers currently grouped in 2D were not saved as 3D.",
                         title="Twinkly 3D mappings captured",
                         notification_id=f"{DOMAIN}_capture_3d")

    async def import_3d_maps(call: ServiceCall):
        from .archive_import import read_archives, merge_archives
        from homeassistant.exceptions import HomeAssistantError
        overwrite = call.data.get("overwrite", False)
        allow_unverified = call.data.get("allow_unverified", False)
        if (overwrite or allow_unverified) and call.data.get("confirmation") != "IMPORT":
            raise HomeAssistantError("Type IMPORT to confirm replacement or unverified import")
        async with lock:
            try:
                incoming = await hass.async_add_executor_job(
                    read_archives, hass.config.path("twinkly_3d_import"))
                identities = {}
                # Public identity reads only: no login, no layout or group writes.
                for device in devices:
                    reply = await query(session, device["ip"], ENDPOINTS["gestalt"])
                    if reply["ok"] and isinstance(reply.get("data"), dict):
                        g = reply["data"]
                        mac = str(g.get("mac", "")).lower().replace(":", "").replace("-", "")
                        count = g.get("number_of_led")
                        if re.fullmatch(r"[0-9a-f]{12}", mac) and type(count) is int and count > 0:
                            identities[mac] = count
                data = await store.async_load() or {}
                archives = data.setdefault("_area_manager", {"maps_3d": {}, "groups": {}})
                merged, added, unchanged, skipped = merge_archives(
                    archives.get("maps_3d", {}), incoming, identities,
                    overwrite=overwrite, allow_unverified=allow_unverified)
                previous = archives.get("maps_3d", {})
                replaced = [mac for mac in previous if mac in merged and merged[mac] != previous[mac]]
                if replaced:
                    backups = archives.setdefault("maps_3d_replacement_backups", {})
                    backups[datetime.now(timezone.utc).isoformat()] = {
                        mac: previous[mac] for mac in replaced}
                unverified = [record["name"] for mac, record in incoming.items()
                              if mac not in identities and mac in merged and merged[mac] == record]
                archives["maps_3d"] = merged
                await store.async_save(data)
                ctx = hass.data[DOMAIN][entry.entry_id]
                for group in ctx.get("virtual_groups", []):
                    group.load_maps(data)
                for bush in ctx.get("combined_bushes", []):
                    bush.load_maps(data)
                for group in ctx.get("virtual_groups", []):
                    group.notify()
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise HomeAssistantError(f"3D archive import failed: {exc}") from exc
            async_create(hass, "Imported: " + (", ".join(added) or "none") +
                         ". Already saved: " + (", ".join(unchanged) or "none") +
                         ". Skipped: " + ("; ".join(skipped) or "none") +
                         f". Total saved 3D references: {len(merged)}. "
                         "Replaced (old references backed up): " +
                         (", ".join(merged[mac]["name"] for mac in replaced) or "none") +
                         ". Imported without live identity verification: " +
                         (", ".join(unverified) or "none") +
                         ". Controller layouts and groups were not changed.",
                         title="Twinkly 3D archive import",
                         notification_id=f"{DOMAIN}_import_3d")

    async def verify_archives(call: ServiceCall):
        async with lock:
            live = await collect("verify-live")
            data = await store.async_load() or {}
            archives = data.get("_area_manager", {})
            if not archives.get("maps_3d") and not archives.get("groups"):
                raise ValueError("No captured group or 3D references to verify")
            issues = _verify_archives(live, archives)
            async_create(hass, ("PASS: current local geometry matches the applicable "
                               "saved references." if not issues else
                               "CHECK: " + "; ".join(issues)) +
                         " Cloud Area verification requires the separate cloud CLI.",
                         title="Twinkly Area verification",
                         notification_id=f"{DOMAIN}_verify")

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

    hass.services.async_register(DOMAIN, "capture_layout_diagnostics", capture_layout_diagnostics)
    hass.services.async_register(DOMAIN, "capture_groups", capture_groups)
    hass.services.async_register(DOMAIN, "capture_3d_maps", capture_3d_maps)
    hass.services.async_register(DOMAIN, "import_3d_maps", import_3d_maps,
        schema=vol.Schema({vol.Optional("overwrite", default=False): bool,
                           vol.Optional("allow_unverified", default=False): bool,
                           vol.Optional("confirmation", default=""): str}))
    hass.services.async_register(DOMAIN, "verify_archives", verify_archives)
    hass.services.async_register(DOMAIN, "save_state", save_state,
        schema=vol.Schema({vol.Required("name"): STATE_NAME}))
    hass.services.async_register(DOMAIN, "restore_state", restore_state,
        schema=vol.Schema({vol.Required("name"): STATE_NAME}))
    hass.services.async_register(DOMAIN, "office_window_plan", office_plan,
        schema=vol.Schema({vol.Required("name"): STATE_NAME,
                           vol.Required("left"): str, vol.Required("right"): str}))
    async def resume_movie(call: ServiceCall):
        for group in hass.data[DOMAIN][entry.entry_id].get("virtual_groups", []):
            await group.stop(movie=True)

    from .cloud_session import CloudSession
    cloud = CloudSession(hass, entry.entry_id)

    async def verify_cloud_session(call: ServiceCall):
        await cloud.get("/v3/installations")
        async_create(hass, "Cloud authentication verified. Cached tokens will renew automatically when required.",
                     title="Twinkly cloud session", notification_id=f"{DOMAIN}_cloud_session")

    async def rescan_controllers(call: ServiceCall):
        # Reload outside the capture lock: platform discovery acquires it.
        if not await hass.config_entries.async_reload(entry.entry_id):
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Twinkly Area Manager reload failed")
        current = hass.data[DOMAIN][entry.entry_id]["devices"]
        async_create(hass, f"Controller inventory: {len(current)} — " +
                     ", ".join(d["name"] for d in current),
                     title="Twinkly controller rescan",
                     notification_id=f"{DOMAIN}_rescan")

    hass.services.async_register(DOMAIN, "rescan_controllers", rescan_controllers)
    hass.services.async_register(DOMAIN, "verify_cloud_session", verify_cloud_session)
    hass.services.async_register(DOMAIN, "resume_movie", resume_movie)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "devices": devices,
        "cloud": cloud,
        "lock": lock,
        "store": store,
        "actions": {"capture_layout_diagnostics": capture_layout_diagnostics,
                    "capture_groups": capture_groups,
                    "capture_3d_maps": capture_3d_maps,
                    "import_3d_maps": import_3d_maps,
                    "verify_archives": verify_archives},
    }
    from .show import Show
    ctx=hass.data[DOMAIN][entry.entry_id]
    show=Show(hass,ctx)
    ctx["show"]=show
    hass.services.async_register(DOMAIN,"start_christmas_show",show.start,schema=vol.Schema({
        vol.Optional("audio",default=True): bool,
        vol.Optional("audio_base",default=""): str,
        vol.Optional("brightness_pct",default=2): vol.All(vol.Coerce(int),vol.Range(min=0,max=100)),
        vol.Optional("volume",default=.05): vol.All(vol.Coerce(float),vol.Range(min=0,max=1)),
        vol.Optional("offset_ms",default=0): vol.All(vol.Coerce(int),vol.Range(min=0,max=2000)),
    }))
    hass.services.async_register(DOMAIN,"stop_christmas_show",show.stop)
    await hass.config_entries.async_forward_entry_setups(entry, ["button", "light"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.data[DOMAIN][entry.entry_id]["show"].stop()
    for group in hass.data[DOMAIN][entry.entry_id].get("virtual_groups", []):
        await group.stop()
    if not await hass.config_entries.async_unload_platforms(entry, ["button", "light"]):
        return False
    for service in ("save_state", "restore_state", "office_window_plan",
                    "capture_groups", "capture_3d_maps", "verify_archives",
                    "capture_layout_diagnostics", "resume_movie", "import_3d_maps", "verify_cloud_session",
                    "start_christmas_show", "stop_christmas_show", "rescan_controllers"):
        hass.services.async_remove(DOMAIN, service)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
