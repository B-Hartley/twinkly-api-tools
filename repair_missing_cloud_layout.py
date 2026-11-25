#!/usr/bin/env python3
"""Repair one missing cloud layout using a verified historical same-device template.

Preview by default. Never writes LAN mappings, never creates/deletes objects.
"""
import argparse
import copy
import datetime as dt
import os
from pathlib import Path
from types import SimpleNamespace

from audit_3d_maps import read
from archive_3d_maps import read_reference, verify_layout
from cloud_audit import cloud_get, objects_from, cached_access_token
from cloud_group_preflight import mac, coordinates, compare_points
from cloud_group_cycle import find_standalone_templates, encoded_layout
from cloud_layout_update import PUT_KEYS, put_object, SELECTOR
from twinkly_inspector import get_devices, save_json

def inventory(token, timeout):
    raw = cloud_get(SELECTOR, token, timeout)
    objects = objects_from(raw)
    if isinstance(raw, dict) and (raw.get("next_page") is not None or
            (isinstance(raw.get("count"), int) and raw["count"] != len(objects))):
        raise ValueError("Incomplete cloud inventory")
    return objects

def target(objects, key, count):
    matches = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        slots = obj.get("devices") or []
        if any(isinstance(s, dict) and isinstance(s.get("device"), dict)
               and mac(s["device"].get("mac")) == key for s in slots):
            matches.append(obj)
    if len(matches) != 1:
        raise ValueError("Expected exactly one cloud object for this MAC")
    obj = matches[0]
    slots = obj["devices"]
    if len(slots) != 1 or obj.get("ledCount") != count or slots[0]["device"].get("ledCount") != count:
        raise ValueError("Cloud identity, membership or LED count mismatch")
    if not str(obj.get("id", "")).isdigit():
        raise ValueError("Invalid cloud object ID")
    return obj

def run(a):
    devices = get_devices(SimpleNamespace(config=a.config, ips=None))
    selected = [d for d in devices if d["name"] == a.name]
    if len(selected) != 1:
        raise ValueError("Name must match one configured controller")
    device = selected[0]
    key, count, group, local = read(device, a.timeout)
    if group.get("mode") != "none":
        raise ValueError("Controller is grouped")
    local = verify_layout(local, count)
    saved = read_reference(a.root, key, count)["layout"]
    if local["synthesized"] != saved["synthesized"] or not compare_points(saved["coordinates"], local["coordinates"])["matching"]:
        raise ValueError("Saved and local layouts disagree")
    token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token(timeout=a.timeout)
    if not token:
        raise ValueError("Cloud login required")
    obj = target(inventory(token, a.timeout), key, count)
    if "layout" not in obj or obj["layout"] is not None:
        raise ValueError("Only an explicitly null cloud layout is eligible")
    plan = {"members": [{"name": a.name, "leds": count, "standalone_layout": saved}]}
    template = find_standalone_templates(plan, [key], a.templates)[a.name]
    layout = copy.deepcopy(template["layout"])
    if not all(k in layout and layout[k] is not None for k in
               ("source", "geometry", "uuid", "aspectXY", "aspectXZ", "name", "synthesized")):
        raise ValueError("Historical cloud layout metadata incomplete")
    if layout["synthesized"] != saved["synthesized"]:
        raise ValueError("Historical synthesized flag differs")
    layout["coords"] = encoded_layout(saved["coordinates"])
    if any(k not in obj for k in PUT_KEYS):
        raise ValueError("Current object lacks required PUT fields")
    payload = {k: copy.deepcopy(obj[k]) for k in PUT_KEYS}
    payload["layout"] = layout
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = Path("cloud-audits") / "layout-writes" / (stamp + "-missing-layout")
    backup.mkdir(parents=True, exist_ok=False)
    save_json(backup / "object-before-private.json", obj)
    save_json(backup / "proposed-put-private.json", payload)
    save_json(backup / "template-private.json", template)
    print(f"Target: {a.name}; object ID: {obj['id']}; LEDs: {count}")
    print(f"Verified historical same-device 3D template; private backups: {backup}")
    if not a.execute:
        print("DRY RUN: no mapping writes")
        return
    if a.confirm_object != str(obj["id"]):
        raise ValueError("Pass --confirm-object with the previewed object ID")
    fresh = target(inventory(token, a.timeout), key, count)
    k2, n2, g2, l2 = read(device, a.timeout)
    if fresh != obj or (k2, n2, g2.get("mode")) != (key, count, "none") or verify_layout(l2, count) != local:
        raise ValueError("Cloud or controller state changed; refusing write")
    put_object(obj["id"], payload, token, a.timeout)
    updated = target(inventory(token, a.timeout), key, count)
    save_json(backup / "object-after-private.json", updated)
    actual = updated.get("layout")
    if (updated.get("id") != obj.get("id") or not isinstance(actual, dict)
            or actual.get("source") != "3d" or actual.get("synthesized") != saved["synthesized"]
            or not compare_points(saved["coordinates"], coordinates(actual), tolerance=0.0003)["matching"]):
        raise RuntimeError("Cloud readback failed; inspect before retrying")
    if any(updated.get(k) != obj.get(k) for k in PUT_KEYS if k != "layout"):
        raise RuntimeError("Cloud non-layout metadata changed; inspect backups before continuing")
    print("VERIFIED: cloud 3D layout matches archive; no LAN writes.")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--config", default="devices.json")
    p.add_argument("--root", default="3d-map-archive")
    p.add_argument("--templates", nargs="+", default=["cloud-audits", "cloud-preflight-backups"])
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--confirm-object", default="")
    a = p.parse_args()
    if not 0 < a.timeout <= 30:
        p.error("Invalid timeout")
    try:
        run(a)
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}")
        print("No automatic retry or rollback. If a PUT was attempted, inspect cloud state.")
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
