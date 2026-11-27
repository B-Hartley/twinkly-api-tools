#!/usr/bin/env python3
"""Read-only three-way audit of saved, LAN and cloud standalone 3D maps."""
import argparse
import os
from controller_workers import add_workers, controller_reads
from types import SimpleNamespace
from audit_3d_maps import read
from archive_3d_maps import read_reference
from cloud_audit import cloud_get, objects_from, cached_access_token
from cloud_group_preflight import mac, coordinates, compare_points
from twinkly_inspector import get_devices

def compare(label, a, b, tolerance):
    result = compare_points(a["coordinates"], b["coordinates"], tolerance=tolerance)
    metadata_ok = all(a.get(k) == b.get(k) for k in ("source", "synthesized"))
    ok = result["matching"] and metadata_ok
    print(f"  {label}: {'MATCH' if ok else 'DIFFERENT'} | LEDs different: {result['mismatched_leds']} | Max delta: {result['max_delta']} | Source: {a.get('source')} / {b.get('source')}")
    if not metadata_ok:
        print("  Mapping metadata differs")
    if not result["matching"]:
        print("  Examples:", result["examples"])
    return ok

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="devices.json")
    p.add_argument("--root", default="3d-map-archive")
    p.add_argument("--timeout", type=float, default=20)
    p.add_argument("--names", nargs="+")
    add_workers(p)
    args = p.parse_args()
    if not 0 < args.timeout <= 30:
        p.error("Invalid timeout")
    token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token(timeout=args.timeout)
    if not token:
        raise SystemExit("Cloud login required. Run cloud_audit.py --plan private-area-plan.json --login first.")
    raw = cloud_get("/v3/objects?mine=true&fields=layout,devices,capabilities", token, args.timeout)
    objects = objects_from(raw)
    if isinstance(raw, dict) and (raw.get("next_page") is not None or (isinstance(raw.get("count"), int) and raw["count"] != len(objects))):
        raise SystemExit("Incomplete cloud inventory")
    devices = get_devices(SimpleNamespace(config=args.config, ips=None))
    if args.names:
        devices = [d for d in devices if d["name"] in args.names]
        if {d["name"] for d in devices} != set(args.names):
            p.error("Unknown device name")
    passed = missing = different = blocked = 0
    for device, result, error in controller_reads(devices, lambda d: read(d, args.timeout), args.workers):
        print(f"\n=== {device['name']} ===")
        try:
            if error is not None: raise error
            key, count, group, local = result
            print(f"  LEDs: {count}")
            if group.get("mode") != "none":
                raise ValueError("Controller is grouped")
            saved = read_reference(args.root, key, count)["layout"]
            local_ok = compare("Saved vs LAN", saved, local, 0.0002)
            matches = [obj for obj in objects if isinstance(obj, dict) and any(
                mac(slot["device"].get("mac")) == key
                for slot in (obj.get("devices") or [])
                if isinstance(slot, dict) and isinstance(slot.get("device"), dict))]
            if len(matches) != 1:
                raise ValueError(f"Expected one cloud object; found {len(matches)}")
            obj = matches[0]
            if len(obj.get("devices") or []) != 1:
                raise ValueError("Cloud object is grouped")
            if obj.get("ledCount") != count:
                raise ValueError("Cloud LED count differs")
            layout = obj.get("layout")
            if layout is None:
                print("  CLOUD MAPPING MISSING: layout is null")
                if local_ok:
                    missing += 1
                else:
                    different += 1
                continue
            cloud = {"source": layout.get("source"), "synthesized": layout.get("synthesized"), "coordinates": coordinates(layout)}
            saved_cloud_ok = compare("Saved vs Cloud", saved, cloud, 0.0003)
            local_cloud_ok = compare("LAN vs Cloud", local, cloud, 0.0003)
            if local_ok and saved_cloud_ok and local_cloud_ok:
                print("  RESULT: ALL THREE MATCH")
                passed += 1
            else:
                print("  RESULT: DISCREPANCY")
                different += 1
        except Exception as exc:
            print(f"  BLOCK: {type(exc).__name__}: {exc}")
            blocked += 1
    print("\n=== FINAL SUMMARY ===")
    print(f"Controllers: {len(devices)}")
    print(f"All three match: {passed}")
    print(f"Cloud mapping missing (saved/LAN match): {missing}")
    print(f"Discrepancies: {different}")
    print(f"Blocked: {blocked}")
    print("No controller, archive or cloud mapping writes performed.")
    return 2 if missing or different or blocked else 0

if __name__ == "__main__":
    raise SystemExit(main())
