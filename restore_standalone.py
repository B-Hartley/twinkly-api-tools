#!/usr/bin/env python3
"""Restore saved standalone 3D LED mappings, even over changed current layouts.

Read-only preflight by default. Live mode requires exact name confirmation.
Does not create, remove or modify group membership. Stops if any member is grouped.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

from group_test import GROUP_API, LAYOUT_API, layout_matches, load_plan
from twinkly_inspector import authenticate, request, save_json

GESTALT = "/xled/v1/gestalt"


def login(ip, timeout):
    token, error = authenticate(ip, timeout)
    if not token:
        raise RuntimeError(f"{ip}: authentication failed: {error}")
    return token


def read_device(member, timeout):
    ip = member["ip"]
    token = login(ip, timeout)
    group = request(ip, GROUP_API, token=token, timeout=timeout)
    layout = request(ip, LAYOUT_API, token=token, timeout=timeout)
    gestalt = request(ip, GESTALT, timeout=timeout)
    if not all(r.get("ok") and isinstance(r.get("data"), dict)
               for r in (group, layout, gestalt)):
        raise RuntimeError(f"{member['name']}: could not read full device state")
    group_obj = group["data"].get("group")
    if not isinstance(group_obj, dict) or group_obj.get("mode") != "none":
        raise RuntimeError(f"{member['name']}: must be ungrouped (mode none), got {group_obj}")
    leds = gestalt["data"].get("number_of_led")
    if leds != member["leds"]:
        raise RuntimeError(f"{member['name']}: hardware reports {leds} LEDs; expected {member['leds']}")
    coords = layout["data"].get("coordinates")
    if not isinstance(coords, list) or len(coords) != member["leds"]:
        raise RuntimeError(f"{member['name']}: current layout LED count invalid")
    return {"ip": ip, "name": member["name"], "group": group["data"],
            "layout": layout["data"], "gestalt": gestalt["data"]}


def classify(member, state):
    if layout_matches(state["layout"], member["standalone_layout"]):
        return "saved standalone 3D (already restored)"
    if layout_matches(state["layout"], member["area_layout"]):
        return "saved Area 2D"
    return "different mapping"


def run(plan, timeout, execute, backup_root, log_path):
    record = {"operation": "restore_standalone", "time_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "execute": execute, "events": []}
    try:
        # Read ALL members before any writes; no changes allowed to group membership.
        states = []
        for member in plan["members"]:
            state = read_device(member, timeout)
            states.append(state)
            print(f"{member['name']}: group=none, LEDs={member['leds']}, "
                  f"layout={classify(member, state)} -> saved standalone 3D")
        if not execute:
            print("READ-ONLY PREVIEW. No controller changes.")
            return 0

        # Save original current state, including any newly created effect-related layout.
        # Filename unique per run, folder ignored by Git.
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_file = backup_root / f"before-standalone-restore-{stamp}.json"
        if backup_file.exists():
            raise RuntimeError("Backup destination exists; refusing overwrite")
        save_json(backup_file, {"captured_utc": record["time_utc"], "devices": states})
        print(f"Pre-write backup saved: {backup_file}")
        record["backup"] = str(backup_file)

        for member, original in zip(plan["members"], states):
            name, ip = member["name"], member["ip"]
            current = read_device(member, timeout)
            if current["layout"] != original["layout"]:
                raise RuntimeError(f"{name}: layout changed since preflight; stopping")
            if layout_matches(current["layout"], member["standalone_layout"]):
                print(f"{name}: already has restored standalone mapping; skipping write")
                record["events"].append({"name": name, "action": "skip", "ok": True})
                continue
            # Freshly authenticate for write, rather than reusing a token from prior GETs.
            token = login(ip, timeout)
            response = request(ip, LAYOUT_API, method="POST", token=token,
                               payload=member["standalone_layout"], timeout=timeout)
            ok = response.get("status") == 200 and response.get("ok") and (
                isinstance(response.get("data"), dict)
                and response["data"].get("code") == 1000)
            record["events"].append({"name": name, "action": "upload standalone 3D",
                                     "status": response.get("status"), "ok": bool(ok),
                                     "app_code": response.get("data", {}).get("code")
                                     if isinstance(response.get("data"), dict) else None,
                                     "error": response.get("error")})
            print(f"{name}: POST layout HTTP {response.get('status')}, success={bool(ok)}")
            if not ok:
                raise RuntimeError(f"{name}: upload failed; stopped, no automatic retry")
            observed = read_device(member, timeout)
            if not layout_matches(observed["layout"], member["standalone_layout"]):
                raise RuntimeError(f"{name}: uploaded layout did not match saved standalone backup")
            record["events"].append({"name": name, "action": "read-back verified", "ok": True})
            print(f"{name}: all {member['leds']} standalone 3D coordinates verified")

        # Final independent verification of BOTH members.
        for member in plan["members"]:
            state = read_device(member, timeout)
            if not layout_matches(state["layout"], member["standalone_layout"]):
                raise RuntimeError(f"{member['name']}: final standalone verification failed")
        print("SUCCESS: both controllers remain ungrouped; standalone 3D mapping restored and verified.")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        record["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if execute:
            save_json(log_path, record)
            print(f"Private execution log: {log_path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", required=True)
    p.add_argument("--execute", action="store_true", help="Actually overwrite layouts")
    p.add_argument("--acknowledge-risk", action="store_true")
    p.add_argument("--group-confirm", help="Exact group_name from private plan")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--backup-root", default="recovery-backups")
    p.add_argument("--log", default="restore-standalone-results.json")
    args = p.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout must be between 0 and 30 seconds")
        plan, _ = load_plan(args.plan, require_verified=args.execute)
        if args.execute and (not args.acknowledge_risk or args.group_confirm != plan["group_name"]):
            raise ValueError("Live writes require --acknowledge-risk and exact --group-confirm")
        return run(plan, args.timeout, args.execute, Path(args.backup_root), Path(args.log))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
