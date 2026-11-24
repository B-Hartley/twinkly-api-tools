#!/usr/bin/env python3
"""Guarded Twinkly cloud object layout updater (unofficial, experimental).

Default: private read-only preview. --execute requires explicit object ID and
acknowledgement. Never changes local controllers or installation membership.
Only supports replacing coordinates with a saved standalone layout when the
cloud object ALREADY has the same 3d source; source/geometry changes require
separate research and are intentionally refused.
"""
import argparse
import base64
import copy
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

from cloud_audit import BASE, NoRedirect, cloud_get, objects_from, decode_coords
from cloud_token_store import renewable_access_token as cached_access_token
from group_test import load_plan
from twinkly_inspector import save_json

SELECTOR = "/v3/objects?mine=true&fields=layout,devices,capabilities"
PUT_KEYS = ("name", "uuid", "ledCount", "devices", "ledProfile",
            "fps", "capacity", "meta", "palette", "layout")
MAX_BODY = 16 * 1024 * 1024


def coords_equal(left, right, tolerance=0.000001):
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
        return False
    try:
        return all(all(abs(float(a[axis]) - float(b[axis])) <= tolerance
                       for axis in ("x", "y", "z"))
                   for a, b in zip(left, right))
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def load_target(objects, member_name, object_id, member):
    matches = [o for o in objects
               if isinstance(o, dict) and str(o.get("id")) == str(object_id)]
    if len(matches) != 1:
        raise ValueError("Object ID was not found exactly once in the current cloud list")
    obj = matches[0]
    devices = obj.get("devices")
    if not isinstance(devices, list) or len(devices) != 1:
        raise ValueError("Target must be a single-controller object; Area objects are refused")
    device = devices[0].get("device") if isinstance(devices[0], dict) else None
    if not isinstance(device, dict):
        raise ValueError("Cloud device identity unavailable")
    normalize = lambda s: " ".join(str(s).split()).casefold()
    if normalize(device.get("name")) != normalize(member_name) or normalize(member.get("name")) != normalize(member_name):
        raise ValueError("Cloud device name does not match chosen plan member")
    if obj.get("ledCount") != member["leds"] or device.get("ledCount") != member["leds"]:
        raise ValueError("Cloud LED counts disagree with saved controller identity")
    if not isinstance(obj.get("layout"), dict) or not isinstance(obj["layout"].get("coords"), str):
        raise ValueError("Cloud object missing full encoded layout")
    if obj["layout"].get("source") != "3d" or member["standalone_layout"].get("source") != "3d":
        raise ValueError("Changing source/geometry from 2d to 3d has not been validated; refusing")
    cloud_points = decode_coords(obj["layout"]["coords"])
    if len(cloud_points) != member["leds"]:
        raise ValueError("Current cloud layout has wrong LED count")
    target = member["standalone_layout"]["coordinates"]
    if not isinstance(target, list) or len(target) != member["leds"]:
        raise ValueError("Saved 3d layout has wrong LED count")
    # Reuse all existing metadata as-is to avoid guessed geometry, UUID, aspect or ownership changes.
    if any(key not in obj for key in PUT_KEYS):
        raise ValueError("Cloud object missing keys required by the captured PUT schema")
    request = {key: copy.deepcopy(obj[key]) for key in PUT_KEYS}
    request["layout"]["coords"] = base64.b64encode(
        gzip.compress(json.dumps(target, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    ).decode("ascii")
    request["layout"]["synthesized"] = member["standalone_layout"].get("synthesized", False)
    return obj, request, cloud_points, target


def put_object(object_id, payload, token, timeout):
    """Single allowlisted object PUT, no redirects or retries."""
    if not str(object_id).isdigit() or not 0 < len(str(object_id)) <= 18:
        raise ValueError("Invalid numeric object ID")
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_BODY:
        raise ValueError("Cloud PUT payload exceeds limit")
    req = urllib.request.Request(
        BASE + "/v3/objects/" + str(object_id), data=body, method="PUT",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "Content-Type": "application/json",
                 "User-Agent": "twinkly-api-tools-layout-update/0.1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Cloud PUT HTTP {response.status}")
            return response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Cloud PUT returned HTTP {error.code}") from None


def run(args):
    plan, _ = load_plan(args.plan)
    members = [m for m in plan["members"] if m["name"].casefold() == args.member.casefold()]
    if len(members) != 1:
        raise ValueError("Member must exactly identify one member in private-area-plan.json")
    if args.execute and not (args.acknowledge_risk and args.confirm_object == str(args.object_id)):
        raise ValueError("Execution requires --acknowledge-risk and --confirm-object matching --object-id")
    token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token(timeout=args.timeout)
    if not token:
        raise ValueError("No valid cached login. Run cloud_audit.py --login first.")
    cloud = cloud_get(SELECTOR, token, args.timeout)
    objects = objects_from(cloud)
    source, proposed, old_points, target = load_target(objects, args.member, args.object_id, members[0])
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = Path(args.backup_root) / (stamp + "-cloud-object-" + str(args.object_id))
    if backup.exists():
        raise ValueError("Backup location already exists")
    backup.mkdir(parents=True, exist_ok=False)
    save_json(backup / "object-before-private.json", source)
    save_json(backup / "proposed-put-private.json", proposed)
    before_hash = hashlib.sha256(json.dumps(old_points, sort_keys=True).encode()).hexdigest()
    after_hash = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
    print(f"Cloud object={args.object_id}; member={args.member}; LEDs={len(target)}; source=3d")
    print(f"Current and desired coordinates identical: {coords_equal(old_points, target)}")
    print(f"Backup and proposed PUT (PRIVATE): {backup}")
    print(f"Current coordinate SHA256={before_hash}; desired coordinate SHA256={after_hash}")
    if not args.execute:
        print("DRY RUN: no cloud or local writes.")
        return 0
    if coords_equal(old_points, target):
        print("No PUT needed: coordinates already match; cloud unchanged.")
        return 0
    # Refetch immediately before writing to avoid overwriting concurrent app changes.
    fresh = cloud_get(SELECTOR, token, args.timeout)
    current, _, latest_points, _ = load_target(
        objects_from(fresh), args.member, args.object_id, members[0])
    if current != source or not coords_equal(latest_points, old_points):
        raise RuntimeError("Cloud object changed since backup; refusing PUT")
    print("Sending ONE cloud object PUT; no automatic retry or rollback.")
    reply = put_object(args.object_id, proposed, token, args.timeout)
    # Do not log arbitrary server response: may contain private account credentials.
    print(f"Cloud PUT accepted (response bytes={len(reply)}); verifying with a fresh GET.")
    verified = cloud_get(SELECTOR, token, args.timeout)
    updated = [o for o in objects_from(verified) if str(o.get("id")) == str(args.object_id)]
    if len(updated) != 1:
        raise RuntimeError("PUT returned 200 but target object could not be read back")
    actual = decode_coords(updated[0]["layout"]["coords"])
    if not coords_equal(actual, target):
        raise RuntimeError("PUT returned 200 but coordinates do not match saved mapping on readback")
    save_json(backup / "object-after-private.json", updated[0])
    print("VERIFIED: cloud object stores the requested 3d coordinates. Local controller untouched.")
    print("App visual refresh and cross-account visibility are NOT yet verified.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", required=True)
    p.add_argument("--member", required=True, help="Exact local plan member name")
    p.add_argument("--object-id", required=True, help="Exact target numeric cloud object ID")
    p.add_argument("--backup-root", default="cloud-audits/layout-writes")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--execute", action="store_true", help="Enable one cloud object PUT")
    p.add_argument("--acknowledge-risk", action="store_true")
    p.add_argument("--confirm-object", default="")
    args = p.parse_args()
    try:
        if not 0 < args.timeout <= 30 or not args.object_id.isdecimal():
            raise ValueError("Invalid timeout or object ID")
        return run(args)
    except (RuntimeError, ValueError, KeyError, TypeError, OSError,
            urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
