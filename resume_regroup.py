#!/usr/bin/env python3
"""Resume a partially completed Twinkly Area regroup safely; read-only preview by default.

Recognises only saved standalone/Area layouts and expected group states.
No automatic retry of a failed POST. Requires explicit flags for writes.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

from group_test import GROUP_API, LAYOUT_API, layout_matches, load_plan
from twinkly_inspector import authenticate, request, save_json


def login(ip, timeout):
    token, error = authenticate(ip, timeout)
    if not token:
        raise RuntimeError(f"Authentication failed: {error}")
    return token


def read_state(member, uid, size, timeout):
    """Fresh auth, read membership and coordinates, verify recognised states."""
    ip = member["ip"]
    token = login(ip, timeout)
    result = request(ip, GROUP_API, token=token, timeout=timeout)
    layout = request(ip, LAYOUT_API, token=token, timeout=timeout)
    if not result.get("ok") or not layout.get("ok"):
        raise RuntimeError(f"{member['name']}: could not read current group/layout")
    group = result.get("data", {}).get("group")
    if not isinstance(group, dict):
        raise RuntimeError(f"{member['name']}: invalid group response")
    mode = group.get("mode")
    if mode == "none":
        group_state = "none"
    elif (mode == ("master" if member["offset"] == 0 else "slave")
          and str(group.get("uid", "")).lower() == uid.lower()
          and group.get("size") == size
          and group.get("offset", 0) == member["offset"]):
        group_state = "expected"
    else:
        raise RuntimeError(f"{member['name']}: unexpected group membership; refusing all writes")
    observed = layout.get("data")
    is_area = layout_matches(observed, member["area_layout"])
    is_standalone = layout_matches(observed, member["standalone_layout"])
    if is_area and not is_standalone:
        layout_state = "area"
    elif is_standalone and not is_area:
        layout_state = "standalone"
    elif is_area and is_standalone:
        layout_state = "area"  # same payload; source match makes this unlikely
    else:
        raise RuntimeError(f"{member['name']}: layout does not match either backup; refusing all writes")
    if group_state == "expected" and layout_state != "area":
        raise RuntimeError(f"{member['name']}: grouped with standalone coordinates; refusing all writes")
    return {"group": group_state, "layout": layout_state}


def steps_for_state(state):
    if state["group"] == "expected":
        return []
    if state["layout"] == "standalone":
        return [LAYOUT_API, GROUP_API]
    if state["layout"] == "area":
        return [GROUP_API]
    raise ValueError("Unknown state")


def group_payload(member, uid, size):
    return {"group": {"mode": "master" if member["offset"] == 0 else "slave",
                      "size": size, "offset": member["offset"], "uid": uid, "compat_mode": 0}}


def check_after(member, uid, size, expected_group, expected_layout, timeout):
    state = read_state(member, uid, size, timeout)
    if state != {"group": expected_group, "layout": expected_layout}:
        raise RuntimeError(f"{member['name']}: post-write mismatch; expected "
                           f"{expected_group}/{expected_layout}, got {state}")
    return state


def run(plan, uid, timeout, execute, log_path):
    record = {"timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "operation": "resume_regroup", "execute": execute, "steps": []}
    try:
        # Preflight the ENTIRE group with independently fresh authentication, no writes.
        states = {}
        tasks = []
        for member in plan["members"]:
            state = read_state(member, uid, plan["size"], timeout)
            states[member["ip"]] = state
            steps = steps_for_state(state)
            tasks.append((member, steps))
            print(f"{member['name']}: group={state['group']}, layout={state['layout']}; "
                  f"needed={', '.join(steps) or 'nothing'}")
        record["preflight"] = [{"name": m["name"], "ip": m["ip"], "state": states[m["ip"]],
                                "needed": paths} for m, paths in tasks]
        if not execute:
            print("READ-ONLY PREVIEW: no LED or group writes sent.")
            return 0
        if not any(steps for _, steps in tasks):
            print("All controllers already in expected group and Area layout.")
            return 0

        # Each write gets a NEW login after checking the exact expected prior state.
        for member, paths in tasks:
            for path in paths:
                before = read_state(member, uid, plan["size"], timeout)
                expected_before = ({"group": "none", "layout": "standalone"}
                                   if path == LAYOUT_API else
                                   {"group": "none", "layout": "area"})
                if before != expected_before:
                    raise RuntimeError(f"{member['name']}: changed since preflight; "
                                       f"expected {expected_before}, got {before}")
                token = login(member["ip"], timeout)
                payload = (member["area_layout"] if path == LAYOUT_API
                           else group_payload(member, uid, plan["size"]))
                response = request(member["ip"], path, method="POST", token=token,
                                   payload=payload, timeout=timeout)
                result = {"name": member["name"], "ip": member["ip"], "endpoint": path,
                          "http_status": response.get("status"),
                          "app_code": response.get("data", {}).get("code")
                          if isinstance(response.get("data"), dict) else None,
                          "ok": bool(response.get("ok")), "error": response.get("error")}
                record["steps"].append(result)
                print(f"{member['name']} {path}: HTTP {result['http_status']}, "
                      f"app_code={result['app_code']}, ok={result['ok']}")
                if not result["ok"]:
                    raise RuntimeError(f"{member['name']}: POST failed; no automatic retry. "
                                       "Use the read-only preview to examine the state.")
                expected_after = ({"group": "none", "layout": "area"}
                                  if path == LAYOUT_API else
                                  {"group": "expected", "layout": "area"})
                check_after(member, uid, plan["size"], expected_after["group"],
                            expected_after["layout"], timeout)
        for member in plan["members"]:
            check_after(member, uid, plan["size"], "expected", "area", timeout)
        print("GROUP REBUILD VERIFIED: all controllers report matching group membership and Area layouts.")
        print("The Twinkly app's friendly name and effects still require visual verification.")
        return 0
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        record["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if execute:
            save_json(log_path, record)
            print(f"Local execution log: {log_path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", required=True)
    p.add_argument("--execute", action="store_true", help="Enable writes; default reads only")
    p.add_argument("--acknowledge-risk", action="store_true")
    p.add_argument("--group-confirm", help="Must exactly match plan group_name for live writes")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--log", default="resume-regroup-results.json")
    args = p.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout must be between 0 and 30 seconds")
        plan, uid = load_plan(args.plan, require_verified=args.execute)
        if args.execute and (not args.acknowledge_risk
                             or args.group_confirm != plan["group_name"]):
            raise ValueError("Live writes require --acknowledge-risk and exact --group-confirm")
        return run(plan, uid, args.timeout, args.execute, Path(args.log))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
