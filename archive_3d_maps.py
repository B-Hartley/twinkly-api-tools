#!/usr/bin/env python3
"""Create and verify immutable per-controller Twinkly 3D reference maps.

Archive is PRIVATE and gitignored. A live standalone 3D controller can be
captured directly; a controller currently grouped in 2D can only be
bootstrapped from an existing, independently saved private plan using
--allow-saved-plan. No map is generated, fabricated or uploaded to lights.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

from cloud_group_preflight import mac, compare_points
from cloud_group_live import read_local
from group_test import load_plan
from twinkly_inspector import get_devices, request, authenticate, save_json

DEFAULT_ROOT = "3d-map-archive"
SCHEMA = 1


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def map_digest(layout):
    return hashlib.sha256(canonical(layout).encode("utf-8")).hexdigest()


def verify_layout(layout, count):
    if not isinstance(layout, dict) or layout.get("source") != "3d":
        raise ValueError("Reference must be an existing 3D mapping, never 2D")
    if type(layout.get("synthesized")) is not bool:
        raise ValueError("3D mapping is missing synthesized flag")
    coords = layout.get("coordinates")
    if not isinstance(coords, list) or len(coords) != count or count < 1 or count > 100000:
        raise ValueError("3D coordinates have incorrect LED count")
    for pt in coords:
        if not isinstance(pt, dict) or any(
            type(pt.get(axis)) not in (float, int) for axis in ("x", "y", "z")
        ):
            raise ValueError("3D coordinates must contain numeric x, y, z")
    # Validates finite values and identical geometry without permitting NaN.
    if not compare_points(coords, coords)["matching"]:
        raise ValueError("Non-finite 3D coordinate detected")
    return {"source": "3d", "synthesized": layout["synthesized"],
            "coordinates": coords}


def identity(gestalt):
    address = mac(gestalt.get("mac")) if isinstance(gestalt, dict) else None
    count = gestalt.get("number_of_led") if isinstance(gestalt, dict) else None
    if not address or type(count) is not int or count <= 0:
        raise ValueError("Controller MAC or LED count unavailable")
    return address, count


def reference_path(root, address):
    if not re.fullmatch("[a-f0-9]{12}", address):
        raise ValueError("Invalid controller hardware identity")
    return Path(root) / "by-mac" / address / "reference-3d.json"


def read_reference(root, address, expected_leds=None):
    path = reference_path(root, address)
    if not path.is_file():
        raise ValueError("Missing immutable 3D reference for controller " +
                         address[-4:] + " under " + str(root))
    content = json.loads(path.read_text(encoding="utf-8"))
    if content.get("schema") != SCHEMA or content.get("mac") != address:
        raise ValueError("3D reference schema or MAC mismatch")
    count = content.get("led_count")
    if type(count) is not int or (expected_leds is not None and count != expected_leds):
        raise ValueError("3D reference LED count mismatch")
    layout = verify_layout(content.get("layout"), count)
    if content.get("sha256") != map_digest(layout):
        raise ValueError("3D reference checksum mismatch; file may have been modified")
    return content


def archive(root, address, count, layout, name, provenance):
    """Create only. Preserve previous reference byte-for-byte; never overwrite."""
    normalized = verify_layout(layout, count)
    dest = reference_path(root, address)
    new_hash = map_digest(normalized)
    if dest.exists():
        old = read_reference(root, address, expected_leds=count)
        if old["sha256"] != new_hash:
            raise ValueError("3D reference already exists with DIFFERENT mapping for " +
                             name + "; refusing overwrite")
        print(name + ": reference already exists and matches; unchanged.")
        return "existing"
    document = {
        "schema": SCHEMA,
        "name_at_archive": str(name),
        "mac": address,
        "led_count": count,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "provenance": provenance,
        "layout": normalized,
        "sha256": new_hash,
        "note": "PRIVATE immutable source of original 3D geometry. Never store in Git.",
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally overwriting a reference.
    with dest.open("x", encoding="utf-8") as output:
        output.write(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(name + ": archived " + str(count) + " LEDs in " + str(dest))
    return "created"


def from_plan(plan_path, root, allow_saved_plan=False, timeout=10):
    plan, _ = load_plan(plan_path)
    created = 0
    for m in plan["members"]:
        current = read_local(m, timeout)
        address, count = identity(current["gestalt"])
        if count != m["leds"]:
            raise ValueError(m["name"] + ": LED count changed")
        local = current["layout"]
        expected = verify_layout(m["standalone_layout"], count)
        if local.get("source") == "3d":
            observed = verify_layout(local, count)
            if not compare_points(observed["coordinates"], expected["coordinates"])["matching"]:
                raise ValueError(m["name"] + ": live 3D differs from the saved plan")
            source = "live-3d-verified-against-private-plan"
            chosen = observed
        elif allow_saved_plan and local.get("source") == "2d":
            # No invention: retain original plan data but do not pretend it was
            # read from the currently grouped 2D controller.
            source = "saved-private-plan-3d-not-current-live-map"
            chosen = expected
        else:
            raise ValueError(m["name"] +
                             ": currently not 3D; use --allow-saved-plan only if original plan was verified")
        if archive(root, address, count, chosen, m["name"], source) == "created":
            created += 1
    return created


def read_device(ip, name, timeout):
    from twinkly_inspector import validate_ip
    ip = validate_ip(ip)
    result = request(ip, "/xled/v1/gestalt", timeout=timeout)
    if not result.get("ok") or not isinstance(result.get("data"), dict):
        raise ValueError(name + ": cannot read hardware identity")
    address, count = identity(result["data"])
    token, error = authenticate(ip, timeout)
    if not token:
        raise ValueError(name + ": local authentication failed")
    result = request(ip, "/xled/v1/led/layout/full", token=token, timeout=timeout)
    if not result.get("ok") or not isinstance(result.get("data"), dict):
        raise ValueError(name + ": cannot read mapping")
    return address, count, result["data"]


def from_devices(config, root, timeout=10):
    class Args:
        ips = None
    args = Args()
    args.config = config
    devices = get_devices(args)
    created = skipped = 0
    for device in devices:
        try:
            address, count, layout = read_device(
                device["ip"], device["name"], timeout)
        except (ValueError, OSError) as exc:
            print(device["name"] + ": SKIPPED (" + str(exc) + ")")
            skipped += 1
            continue
        if layout.get("source") != "3d":
            print(device["name"] + ": SKIPPED (currently not a 3D layout)")
            skipped += 1
            continue
        if archive(root, address, count, layout, device["name"],
                   "captured-live-standalone-3d") == "created":
            created += 1
    return created, skipped


def verify_plan(plan, root, live=False, timeout=10):
    plan, _ = load_plan(plan)
    entries = []
    for member in plan["members"]:
        current = read_local(member, timeout) if live else None
        if not current:
            raise ValueError("Verifying archives against plan requires --live identity check")
        address, count = identity(current["gestalt"])
        ref = read_reference(root, address, count)
        original = verify_layout(member["standalone_layout"], count)
        if not compare_points(ref["layout"]["coordinates"],
                              original["coordinates"])["matching"]:
            raise ValueError(member["name"] + ": archive differs from private plan")
        entries.append(ref)
        print(member["name"] + ": 3D reference valid, " + str(count) +
              " LEDs, provenance=" + ref["provenance"])
    return entries


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", help="Archive the members of a private grouped/standalone plan")
    modes.add_argument("--devices", help="Capture live 3D mappings of all devices in a private devices.json")
    modes.add_argument("--verify-plan", help="Check archived references against private plan + live hardware identity")
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--allow-saved-plan", action="store_true",
                   help="Allow saved original 3D plan when current controllers are grouped 2D")
    p.add_argument("--timeout", type=float, default=10)
    args = p.parse_args(argv)
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout out of range")
        if args.verify_plan:
            verify_plan(args.verify_plan, args.root, live=True, timeout=args.timeout)
        elif args.plan:
            n = from_plan(args.plan, args.root, args.allow_saved_plan, args.timeout)
            print("New immutable 3D references:", n)
        else:
            created, skipped = from_devices(args.devices, args.root, args.timeout)
            print("New references:", created, "| skipped:", skipped)
        print("READ ONLY: no controller or cloud mappings modified.")
        return 0
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
