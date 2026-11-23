#!/usr/bin/env python3
"""Recover Office controllers after local grouping succeeded but cloud stayed standalone.

Read-only by default. Explicit execution changes ONLY local Twinkly group and
layout endpoints; it never issues cloud writes or deletes cloud objects.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import urllib.error

import archive_3d_maps as refs
import cloud_group_cycle as ops
from cloud_group_live import collect, read_local
from cloud_group_preflight import FILES, compare_points, coordinates, mac, objects_from, normalized_uid
from cloud_group_cycle_reverse import archived_group_reference, archived_layouts, identities
from group_test import load_plan, layout_matches
from twinkly_inspector import save_json


def validate_mixed(snapshot, plan):
    """Precisely require cloud standalone+local grouped before any local write."""
    ids = identities(snapshot, plan)
    refs_3d = archived_layouts(plan, ids, "3d-map-archive")
    area, refs_2d = archived_group_reference(
        plan, ids, ("cloud-preflight-backups", "cloud-audits"))
    objs = objects_from(snapshot[FILES[0]])
    clouds = []
    for i, addr in enumerate(ids):
        matches = [o for o in objs if isinstance(o.get("devices"), list)
                   and len(o["devices"]) == 1
                   and mac(o["devices"][0].get("device", {}).get("mac")) == addr]
        if len(matches) != 1:
            raise ValueError("Cloud standalone count/identity changed for " + plan["members"][i]["name"])
        obj = matches[0]
        if (obj.get("ledCount") != plan["members"][i]["leds"]
                or not isinstance(obj.get("layout"), dict)
                or obj["layout"].get("source") != "3d"
                or not compare_points(coordinates(obj["layout"]),
                                      refs_3d[i]["coordinates"])["matching"]):
            raise ValueError("Cloud 3D mapping does not match immutable reference for " +
                             plan["members"][i]["name"])
        clouds.append(obj)
    # Reject cloud Area duplicates or any second object containing same controller.
    for obj in objs:
        members = obj.get("devices") if isinstance(obj, dict) else None
        if not isinstance(members, list) or obj in clouds:
            continue
        if any(mac(slot.get("device", {}).get("mac")) in ids
               for slot in members if isinstance(slot, dict)):
            raise ValueError("Unexpected additional cloud membership; refuse recovery")
    uids = []
    for i, member in enumerate(plan["members"]):
        local = snapshot[FILES[i + 1]]
        role = local["group"].get("group", {})
        required_role = "master" if i == 0 else "slave"
        if (role.get("mode") != required_role or
                role.get("size") != plan["size"] or
                (i == 1 and role.get("offset") != member["offset"])):
            raise ValueError(member["name"] + ": local grouped role changed")
        uid = normalized_uid(role.get("uid"))
        if uid is None:
            raise ValueError(member["name"] + ": invalid group UUID")
        uids.append(uid)
        layout = local["layout"]
        if (layout.get("source") != "2d" or
                not compare_points(layout.get("coordinates", []),
                                   refs_2d[i]["coordinates"])["matching"]):
            raise ValueError(member["name"] + ": local 2D layout differs from reference")
    if uids[0] != uids[1]:
        raise ValueError("Controllers have differing group UUIDs")
    print("RECOVERY READY: two original 3D cloud objects unchanged,")
    print("two local controllers grouped with archived 2D layout.")
    print("Will restore 3D layouts from immutable archives without cloud writes.")
    return refs_3d, ids


def run(args):
    if not 0 < args.timeout <= 30:
        raise ValueError("Invalid timeout")
    plan, _ = load_plan(args.plan)
    if len(plan["members"]) != 2:
        raise ValueError("Exactly two devices required")
    if not args.execute:
        report, plan, snapshot = collect(args.plan, timeout=args.timeout,
                                         output=args.output, include_snapshot=True)
        validate_mixed(snapshot, plan)
        print("DRY RUN: no cloud or controller writes. Use --execute --acknowledge-risk.")
        return 0
    if not args.acknowledge_risk:
        raise ValueError("Execution requires --acknowledge-risk")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(args.output) / ("local-recovery-" + stamp)
    destination.mkdir(parents=True, exist_ok=False)
    status = {"complete": False, "stages": []}
    try:
        _, plan, snapshot = collect(args.plan, timeout=args.timeout,
                                    output=destination, include_snapshot=True)
        maps, ids = validate_mixed(snapshot, plan)
        save_json(destination / "local-recovery-status-private.json", status)
        ops.approve("This will UNGROUP the two controllers LOCALLY only and restore their archived 3D layouts. "
                    "The cloud will NOT be modified.", "RESTORE")
        for i, member in enumerate(plan["members"]):
            # Verify current role is still the captured role, and saved 2D
            # coordinates are unchanged, immediately before first write.
            latest = read_local(member, args.timeout)
            if (latest["group"]["group"].get("mode") !=
                    ("master" if i == 0 else "slave") or
                    not layout_matches(latest["layout"],
                                       snapshot[FILES[i + 1]]["layout"])):
                raise RuntimeError(member["name"] + ": local state changed after validation")
            ops.local_write(member, ops.GROUP_API,
                            {"group": {"mode": "none", "size": 0, "offset": 0}},
                            args.timeout)
            ops.local_write(member, ops.LAYOUT_API, maps[i], args.timeout)
            observed = read_local(member, args.timeout)
            if (observed["group"]["group"].get("mode") != "none" or
                    not layout_matches(observed["layout"], maps[i])):
                raise RuntimeError(member["name"] + ": standalone local readback failed")
            status["stages"].append(member["name"] + " standalone 3D restored")
            save_json(destination / "local-recovery-status-private.json", status)
        report, plan, final = collect(args.plan, timeout=args.timeout,
                                      output=destination, include_snapshot=True)
        if (report["result"] != "PASS_READ_ONLY" or report["state"] != "ungrouped-cloud"
                or identities(final, plan) != ids):
            raise RuntimeError("Final cloud/local standalone preflight failed")
        status["complete"] = True
        save_json(destination / "local-recovery-status-private.json", status)
        print("RECOVERY PASS: both controllers standalone, cloud unchanged, 3D archives restored.")
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError,
            urllib.error.URLError) as exc:
        status["error"] = str(exc)
        print("STOPPED:", exc, file=sys.stderr)
        print("Do not rerun after partial changes. Inspect recovery snapshots.", file=sys.stderr)
        return 2
    finally:
        save_json(destination / "local-recovery-status-private.json", status)
        print("Private recovery backups:", destination)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", default="private-area-plan.json")
    p.add_argument("--output", default="cloud-preflight-backups")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--acknowledge-risk", action="store_true")
    args = p.parse_args()
    try:
        return run(args)
    except (ValueError, RuntimeError, KeyError, OSError, TypeError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
