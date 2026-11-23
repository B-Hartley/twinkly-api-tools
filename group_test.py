#!/usr/bin/env python3
"""Guarded Area group ungroup/regroup CLI. Dry-run default; never writes without --execute."""
import argparse
import json
import math
from pathlib import Path
import sys
import time
import uuid

from twinkly_inspector import authenticate, request, save_json, validate_ip

GROUP_API = "/xled/v1/application/group"
LAYOUT_API = "/xled/v1/led/layout/full"

def load_plan(path, require_verified=False):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("schema") != 1:
        raise ValueError("Unsupported plan schema")
    members = plan.get("members")
    if not isinstance(members, list) or len(members) < 2:
        raise ValueError("Plan requires two or more members")
    uid = str(uuid.UUID(plan["uid"]))
    seen_ips, seen_offsets = set(), set()
    total = 0
    for m in members:
        ip = validate_ip(m["ip"])
        if ip in seen_ips:
            raise ValueError("Duplicate controller IP")
        seen_ips.add(ip)
        n = m["leds"]
        offset = m["offset"]
        if type(n) is not int or n <= 0 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid LED count or offset")
        if offset in seen_offsets:
            raise ValueError("Duplicate offset")
        seen_offsets.add(offset)
        total += n
        for field in ("standalone_layout", "area_layout"):
            layout = m.get(field)
            if not isinstance(layout, dict) or layout.get("source") not in ("2d", "3d"):
                raise ValueError(f"{m.get('name', ip)}: missing/invalid {field}")
            coords = layout.get("coordinates")
            if not isinstance(coords, list) or len(coords) != n:
                raise ValueError(f"{m.get('name', ip)}: {field} has wrong coordinate count")
            if not all(isinstance(c, dict) for c in coords):
                raise ValueError(f"{m.get('name', ip)}: coordinates must be objects")
            if layout.get("synthesized") not in (True, False):
                raise ValueError(f"{m.get('name', ip)}: synthesized must be boolean")
    if sorted((m["offset"], m["offset"] + m["leds"]) for m in members) != [
        (sum(x["leds"] for x in sorted(members, key=lambda x: x["offset"])[:i]),
         sum(x["leds"] for x in sorted(members, key=lambda x: x["offset"])[:i + 1]))
        for i, m in enumerate(sorted(members, key=lambda x: x["offset"]))
    ]:
        raise ValueError("Offsets do not form a contiguous LED sequence")
    if members[0]["offset"] != 0 or sum(m["offset"] == 0 for m in members) != 1:
        raise ValueError("Master must be first with offset 0")
    if plan.get("size") != total:
        raise ValueError("Group size must equal sum of member LEDs")
    if require_verified and plan.get("preverified_backup") is not True:
        raise ValueError("Live execution requires preverified_backup=true after manual snapshot comparison")
    return plan, uid


def payloads(plan, command, uid):
    for index, m in enumerate(plan["members"]):
        if command == "ungroup":
            group = {"group": {"mode": "none", "size": 0, "offset": 0}}
            layout = m["standalone_layout"]
        else:
            group = {"group": {"mode": "master" if index == 0 else "slave",
                               "size": plan["size"], "offset": m["offset"],
                               "uid": uid, "compat_mode": 0}}
            layout = m["area_layout"]
        # App capture showed layout before group assignment on regroup;
        # for ungroup the captured command order needs separate validation.
        if command == "ungroup":
            yield m, [(GROUP_API, group), (LAYOUT_API, layout)]
        else:
            yield m, [(LAYOUT_API, layout), (GROUP_API, group)]


def layout_matches(observed, expected, tolerance=0.0002):
    """Ignore generated UUID/code; compare source and every LED coordinate."""
    if not isinstance(observed, dict) or observed.get("source") != expected.get("source"):
        return False
    if observed.get("synthesized") != expected.get("synthesized"):
        return False
    coords = observed.get("coordinates")
    reference = expected.get("coordinates")
    if not isinstance(coords, list) or len(coords) != len(reference):
        return False
    for point, saved in zip(coords, reference):
        if not isinstance(point, dict):
            return False
        for axis in ("x", "y", "z"):
            try:
                actual, original = point[axis], saved[axis]
                if not all(math.isfinite(x) for x in (actual, original)):
                    return False
                if abs(actual - original) > tolerance:
                    return False
            except (KeyError, TypeError):
                return False
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["ungroup", "regroup"])
    p.add_argument("--plan", required=True, help="Private reviewed plan with BOTH standalone and Area layouts")
    p.add_argument("--execute", action="store_true", help="Enable live writes (default is dry run)")
    p.add_argument("--acknowledge-risk", action="store_true", help="Acknowledge irreversible partial-state risk")
    p.add_argument("--group-confirm", help="Enter exact group_name from the plan to authorize execution")
    p.add_argument("--timeout", type=float, default=5)
    p.add_argument("--log", default="group-test-results.json")
    args = p.parse_args()
    try:
        plan, uid = load_plan(args.plan, require_verified=args.execute)
        if args.timeout <= 0 or args.timeout > 30:
            raise ValueError("timeout must be between 0 and 30 seconds")
        sequences = list(payloads(plan, args.command, uid))
        print(f"Operation: {args.command}; group: {plan['group_name']}; members: {len(sequences)}")
        for m, calls in sequences:
            print(f"  {m['name']} ({m['ip']}) offset={m['offset']}: "
                  + " -> ".join(path for path, _ in calls))
        if not args.execute:
            print("DRY RUN ONLY: no device requests sent. No controller state changed.")
            return 0
        if not args.acknowledge_risk or args.group_confirm != plan["group_name"]:
            raise ValueError("Live write requires --acknowledge-risk and --group-confirm matching group_name")
        if not Path(args.log).resolve().parent.exists():
            raise ValueError("Log directory does not exist")
        # Preflight expected group state on every controller before any writes.
        tokens = {}
        for m, _ in sequences:
            ip = m["ip"]
            info = request(ip, "/xled/v1/gestalt", timeout=args.timeout)
            if not info.get("ok"):
                raise RuntimeError(f"Preflight unavailable: {m['name']}: {info.get('error', info)}")
            data = info.get("data") or {}
            observed_leds = data.get("number_of_led") if isinstance(data, dict) else None
            if observed_leds is not None and observed_leds != m["leds"]:
                raise RuntimeError(f"LED count mismatch for {m['name']}: {observed_leds} != {m['leds']}")
            token, error = authenticate(ip, args.timeout)
            if not token:
                raise RuntimeError(f"Authentication failed for {m['name']}: {error}")
            tokens[ip] = token
            summary = request(ip, GROUP_API, token=token, timeout=args.timeout)
            if not summary.get("ok") or not isinstance(summary.get("data"), dict):
                raise RuntimeError(f"Missing preflight summary: {m['name']}")
            group = summary["data"].get("group")
            if not isinstance(group, dict):
                raise RuntimeError(f"Unrecognised group state: {m['name']}")
            if args.command == "ungroup":
                expected_mode = "master" if m["offset"] == 0 else "slave"
                if (str(group.get("uid", "")).lower() != uid.lower()
                        or group.get("mode") != expected_mode
                        or group.get("size") != plan["size"]
                        or (group.get("offset", 0) if m["offset"] == 0 else group.get("offset")) != m["offset"]):
                    raise RuntimeError(f"Not in expected group: {m['name']}")
            elif group.get("mode") != "none":
                raise RuntimeError(f"Not standalone before regroup: {m['name']}")
            start_layout = m["area_layout"] if args.command == "ungroup" else m["standalone_layout"]
            before_layout = request(ip, LAYOUT_API, token=token, timeout=args.timeout)
            if not before_layout.get("ok") or not layout_matches(before_layout.get("data"), start_layout):
                raise RuntimeError(
                    f"{m['name']}: current layout differs from the saved starting layout; refusing writes")
        results = {"command": args.command, "group_name": plan["group_name"], "results": [],
                   "warning": "A successful HTTP response is not proof of restored Area app geometry"}
        print("Preflight passed. Sending controller writes. Do not interrupt.")
        try:
            for m, calls in sequences:
                for path, data in calls:
                    response = request(m["ip"], path, method="POST", token=tokens[m["ip"]],
                                       payload=data, timeout=args.timeout)
                    record = {"name": m["name"], "ip": m["ip"], "endpoint": path,
                              "ok": response.get("ok", False),
                              "status": response.get("status"),
                              "application_code": (response.get("data") or {}).get("code")
                              if isinstance(response.get("data"), dict) else None,
                              "error": response.get("error")}
                    results["results"].append(record)
                    if not response.get("ok"):
                        raise RuntimeError(f"STOPPED at {m['name']} {path}: {record}")
            for m, _ in sequences:
                observed = request(m["ip"], GROUP_API,
                                   token=tokens[m["ip"]], timeout=args.timeout)
                group = observed.get("data", {}).get("group") if observed.get("ok") else None
                expected_mode = ("none" if args.command == "ungroup" else
                                 "master" if m["offset"] == 0 else "slave")
                verified = isinstance(group, dict) and group.get("mode") == expected_mode
                if args.command == "regroup":
                    verified = (verified and str(group.get("uid", "")).lower() == uid.lower()
                                and group.get("size") == plan["size"]
                                and (group.get("offset", 0) if m["offset"] == 0 else group.get("offset")) == m["offset"])
                results["results"].append({"name": m["name"], "verification": "application_group",
                                           "ok": verified, "group": group})
                if not verified:
                    raise RuntimeError(f"Post-write group verification failed for {m['name']}")
                after_layout = request(m["ip"], LAYOUT_API, token=tokens[m["ip"]],
                                       timeout=args.timeout)
                expected_layout = (m["standalone_layout"] if args.command == "ungroup"
                                   else m["area_layout"])
                layout_verified = (after_layout.get("ok") and
                                   layout_matches(after_layout.get("data"), expected_layout))
                results["results"].append({"name": m["name"], "verification": "led_layout_full",
                                           "ok": bool(layout_verified)})
                if not layout_verified:
                    raise RuntimeError(f"Post-write layout verification failed for {m['name']}")
        finally:
            # Logs can contain identifiers: keep locally and never commit.
            save_json(Path(args.log), results)
        print(f"Requests finished; inspect {args.log} and the lights before any further action.")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
