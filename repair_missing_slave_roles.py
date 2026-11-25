#!/usr/bin/env python3
"""Guarded repair of missing slave roles in a multi-controller Twinkly Area.

Read-only preview by default. Never modifies cloud or LED layout geometry.
"""
import argparse
import datetime as dt
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

from audit_3d_maps import read
from archive_3d_maps import read_reference
from cloud_audit import cloud_get, objects_from, cached_access_token
from cloud_group_preflight import mac, coordinates, compare_points
from cloud_group_cycle import local_write, GROUP_API
from twinkly_inspector import get_devices, save_json

def collect(a, token):
    raw = cloud_get("/v3/objects?mine=true&fields=layout,devices,capabilities", token, a.timeout)
    objects = objects_from(raw)
    if isinstance(raw, dict) and (raw.get("next_page") is not None or
            (isinstance(raw.get("count"), int) and raw["count"] != len(objects))):
        raise ValueError("Incomplete cloud inventory")
    matches = [o for o in objects if str(o.get("id")) == a.object_id and o.get("name") == a.group]
    if len(matches) != 1:
        raise ValueError("Exact cloud object ID and name must match")
    obj = matches[0]
    slots = sorted(obj["devices"], key=lambda s: s["part"])
    if len(slots) < 2 or [s["part"] for s in slots] != list(range(len(slots))):
        raise ValueError("Invalid cloud member part ordering")
    if obj["layout"]["source"] != "2d":
        raise ValueError("Cloud layout is not 2D")
    ids = [mac(s["device"].get("mac")) for s in slots]
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("Invalid or duplicate cloud identity")
    for other in objects:
        if other is obj:
            continue
        if any(mac((s.get("device") or {}).get("mac")) in ids for s in (other.get("devices") or [])):
            raise ValueError("Cloud member also belongs to another returned object")
    devices = get_devices(SimpleNamespace(config=a.config, ips=None))
    observations = {}
    for d in devices:
        key, count, role, layout = read(d, a.timeout)
        if key in observations:
            raise ValueError("Duplicate local identity")
        observations[key] = (d, count, role, layout)
    if not set(ids).issubset(observations):
        raise ValueError("Not all cloud members are locally reachable")
    total = obj["ledCount"]
    points = coordinates(obj["layout"])
    if len(points) != total:
        raise ValueError("Cloud coordinate count differs")
    master = observations[ids[0]][2]
    uid = str(uuid.UUID(str(master.get("uid"))))
    missing = {}
    evidence = {"cloud_id": obj["id"], "cloud_points": points, "members": []}
    offset = 0
    for i, slot in enumerate(slots):
        key = ids[i]
        d, count, role, layout = observations[key]
        read_reference(a.root, key, count)
        if count != slot["device"]["ledCount"] or layout.get("source") != "2d" or len(layout["coordinates"]) != count:
            raise ValueError(d["name"] + ": identity/count/source mismatch")
        comparison = compare_points(points[offset:offset+count], layout["coordinates"], tolerance=0.0003)
        if not comparison["matching"]:
            raise ValueError(d["name"] + ": cloud/LAN geometry mismatch")
        expected = {"mode": "master" if i == 0 else "slave", "size": total,
                    "offset": offset, "uid": uid, "compat_mode": 0}
        mode = role.get("mode")
        if i > 0 and mode == "none":
            missing[d["name"]] = (d, expected)
        else:
            normalized = dict(role)
            # Master firmware omits offset; its implicit start is zero.
            # Never infer a missing slave offset.
            if i == 0 and mode == "master" and "offset" not in normalized:
                normalized["offset"] = 0
            normalized["uid"] = str(uuid.UUID(str(role.get("uid"))))
            if any(normalized.get(k) != v for k, v in expected.items()):
                raise ValueError(d["name"] + ": unexpected existing group role/size/offset/UUID")
        evidence["members"].append({"name": d["name"], "ip": d["ip"], "mac": key,
            "count": count, "role": role, "layout": layout, "expected": expected})
        print(f"{d['name']}: {count} LEDs | offset {offset} | {mode} | geometry MATCH")
        offset += count
    if offset != total:
        raise ValueError("Member LED counts do not sum to cloud total")
    return missing, evidence, obj

def backup(evidence, obj):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = Path("cloud-preflight-backups") / ("slave-repair-" + stamp)
    dest.mkdir(parents=True, exist_ok=False)
    save_json(dest / "cloud-object-private.json", obj)
    save_json(dest / "local-evidence-private.json", evidence)
    print("Private backup:", dest)

def run(a):
    token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token(timeout=a.timeout)
    if not token:
        raise ValueError("Cloud login required")
    missing, before, obj = collect(a, token)
    backup(before, obj)
    print("Missing slave roles:", ", ".join(missing) or "none")
    if not missing:
        print("PASS: all group roles and coordinates match")
        return
    if not a.execute:
        print("DRY RUN: no changes made")
        return
    if set(a.confirm_names or []) != set(missing):
        raise ValueError("Confirm exactly the missing slave names from preview")
    latest, evidence, obj = collect(a, token)
    backup(evidence, obj)
    if evidence != before or latest != missing:
        raise ValueError("Group state changed since preflight")
    for name in list(missing):
        # Each write is individually verified before proceeding.
        d, expected = latest[name]
        key, count, role, layout = read(d, a.timeout)
        original = next(m for m in evidence["members"] if m["name"] == name)
        if (key, count, role, layout) != (original["mac"], original["count"], original["role"], original["layout"]):
            raise ValueError(name + ": state changed before write")
        print("Writing slave role only:", name)
        local_write(d, GROUP_API, {"group": expected}, a.timeout)
        remaining, after, updated = collect(a, token)
        backup(after, updated)
        if name in remaining or set(remaining) != set(latest) - {name}:
            raise RuntimeError("Unexpected post-write role state")
        if not compare_points(before["cloud_points"], after["cloud_points"])["matching"]:
            raise RuntimeError("Cloud coordinates changed")
        for old, new in zip(evidence["members"], after["members"]):
            if old["mac"] != new["mac"] or old["layout"] != new["layout"]:
                raise RuntimeError("Local identity or geometry changed")
            if old["name"] != name and old["role"] != new["role"]:
                raise RuntimeError("Unexpected change to another role")
        latest, evidence = remaining, after
    print("PASS: all group roles and coordinates match; no cloud or layout writes")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group", required=True)
    p.add_argument("--object-id", required=True)
    p.add_argument("--config", default="devices.json")
    p.add_argument("--root", default="3d-map-archive")
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--confirm-names", nargs="+")
    a = p.parse_args()
    if not 0 < a.timeout <= 30 or not a.object_id.isdecimal():
        p.error("Invalid timeout or object ID")
    try:
        run(a)
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}")
        print("No automatic retry or rollback. Inspect state if a write was attempted.")
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
