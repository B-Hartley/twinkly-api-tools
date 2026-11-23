#!/usr/bin/env python3
"""Read-only group-state and layout audit after an interrupted Area operation."""
import argparse
import json
import sys

from group_test import load_plan, layout_matches
from twinkly_inspector import authenticate, request

GROUP = "/xled/v1/application/group"
LAYOUT = "/xled/v1/led/layout/full"


def audit(plan, timeout):
    failures = 0
    for member in plan["members"]:
        name, ip = member["name"], member["ip"]
        token, error = authenticate(ip, timeout)
        if not token:
            failures += 1
            print(f"{name}: authentication unavailable: {error}")
            continue
        membership = request(ip, GROUP, token=token, timeout=timeout)
        layout = request(ip, LAYOUT, token=token, timeout=timeout)
        group = membership.get("data", {}).get("group") if membership.get("ok") else None
        if not isinstance(group, dict):
            failures += 1
            print(f"{name}: group GET failed: {membership.get('error', membership.get('status', 'unknown'))}")
        else:
            print(f"{name}: mode={group.get('mode', '?')} size={group.get('size', '-')} "
                  f"offset={group.get('offset', 0) if group.get('mode') == 'master' else group.get('offset', '-')} "
                  f"UID={'matching plan' if str(group.get('uid', '')).lower() == plan['uid'].lower() else 'absent/different'}")
        if not layout.get("ok"):
            failures += 1
            print(f"  layout read failed: {layout.get('error', layout.get('status', 'unknown'))}")
        else:
            data = layout["data"]
            matches_area = layout_matches(data, member["area_layout"])
            matches_standalone = layout_matches(data, member["standalone_layout"])
            state = ("area" if matches_area and not matches_standalone else
                     "standalone" if matches_standalone and not matches_area else
                     "both" if matches_area and matches_standalone else "NEITHER SAVED LAYOUT")
            print(f"  layout={state}, source={data.get('source')}, LEDs={len(data.get('coordinates', []))}")
            if not (matches_area or matches_standalone):
                failures += 1
    return 0 if failures == 0 else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout must be 0 < seconds <= 30")
        plan, _ = load_plan(args.plan)
        return audit(plan, args.timeout)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
