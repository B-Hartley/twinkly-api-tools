#!/usr/bin/env python3
"""Read-only cloud/LAN consistency check for a two-controller Twinkly Area.

No group or object writes. Local login/verify is used to read controller state.
A new Area UUID is accepted if BOTH controllers agree on it.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error

from cloud_audit import cloud_get, objects_from
from cloud_token_store import access_token as cached_access_token
from group_test import load_plan
from twinkly_inspector import authenticate, request, save_json

SELECTOR = "/v3/objects?mine=true&fields=layout,devices,capabilities"


def normalized(value):
    return " ".join(str(value or "").split()).casefold()


def members_in_object(obj):
    result = []
    for part in obj.get("devices", []):
        if isinstance(part, dict):
            device = part.get("device")
            if isinstance(device, dict):
                result.append(normalized(device.get("name")))
    return result


def cloud_state(objects, members, size):
    expected = {normalized(m["name"]) for m in members}
    singles, groups = [], []
    for obj in objects:
        if not isinstance(obj, dict) or not isinstance(obj.get("devices"), list):
            continue
        names = members_in_object(obj)
        if len(names) == len(members) and set(names) == expected and obj.get("ledCount") == size:
            groups.append(obj)
        elif len(names) == 1 and names[0] in expected:
            singles.append(obj)
    if len(groups) == 1 and not singles:
        return "joined", groups
    if not groups and len(singles) == len(members) and {members_in_object(o)[0] for o in singles} == expected:
        return "unjoined", singles
    return "ambiguous", groups + singles


def local_state(plan, timeout):
    rows = []
    for member in plan["members"]:
        token, error = authenticate(member["ip"], timeout)
        if not token:
            raise RuntimeError(f"{member['name']}: controller authentication failed")
        values = {}
        for name, path in (("group", "/xled/v1/application/group"),
                           ("layout", "/xled/v1/led/layout/full"),
                           ("gestalt", "/xled/v1/gestalt")):
            response = request(member["ip"], path, token=token, timeout=timeout)
            if not response.get("ok") or not isinstance(response.get("data"), dict):
                raise RuntimeError(f"{member['name']}: could not GET {name}")
            values[name] = response["data"]
        g = values["group"].get("group")
        layout = values["layout"]
        if not isinstance(g, dict):
            raise RuntimeError(f"{member['name']}: malformed group response")
        if values["gestalt"].get("number_of_led") != member["leds"] or len(layout.get("coordinates", [])) != member["leds"]:
            raise RuntimeError(f"{member['name']}: LED count mismatch")
        role = "master" if member["offset"] == 0 else "slave"
        mode = g.get("mode")
        joined = (mode == role and bool(g.get("uid")) and g.get("size") == plan["size"]
                  and g.get("offset", 0) == member["offset"])
        state = "joined" if joined else ("unjoined" if mode == "none" else "unexpected")
        rows.append({"name": member["name"], "state": state, "mode": mode,
                     "uid": g.get("uid"), "source": layout.get("source"),
                     "synthesized": layout.get("synthesized"), "leds": member["leds"]})
    state = "ambiguous"
    if all(r["state"] == "unjoined" for r in rows):
        state = "unjoined"
    elif all(r["state"] == "joined" for r in rows) and len({str(r["uid"]).lower() for r in rows}) == 1:
        state = "joined"
    return state, rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", default="private-area-plan.json")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--output", default="cloud-audits/office-consistency-PRIVATE.json")
    args = p.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout out of range")
        plan, _ = load_plan(args.plan)
        if len(plan["members"]) != 2 or plan["size"] != 420:
            raise ValueError("Expected two Office controllers and a 420-LED Area")
        token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token()
        if not token:
            raise RuntimeError("Cloud token unavailable. Run cloud_audit.py --login first.")
        raw = cloud_get(SELECTOR, token, args.timeout)
        objects = objects_from(raw)
        cloud, matching = cloud_state(objects, plan["members"], plan["size"])
        local, rows = local_state(plan, args.timeout)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, {"cloud": cloud, "local": local, "local_controllers": rows,
                         "office_cloud_object_ids": [o.get("id") for o in matching]})
        print("Cloud Office state:", cloud, "(" + str(len(matching)) + " matching cloud objects)")
        for row in rows:
            print(f"{row['name']}: {row['state']} / mode={row['mode']} / "
                  f"source={row['source']} / synthesized={row['synthesized']}")
        print("Local Office state:", local)
        if cloud == local and cloud != "ambiguous":
            print("CONSISTENT: cloud and both local controllers agree on the group state.")
            return 0
        print("INCONSISTENT: do not run regroup/ungroup automation or cloud writes.")
        print("Local private diagnostic:", path)
        return 2
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
