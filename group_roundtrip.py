#!/usr/bin/env python3
"""Safe Twinkly Check -> Ungroup -> Check -> Group -> Check round trip.

The two destructive group transitions are performed in the Twinkly app.
This script checks and backs up live cloud + LAN state at each checkpoint,
and verifies both original standalone maps and returned group geometry.
It NEVER writes cloud objects or controller settings itself. A true fully
automatic cloud write workflow needs separately verified create/remove APIs.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

from cloud_group_live import collect
from cloud_group_preflight import FILES, compare_points, coordinates, mac, objects_from
from group_test import layout_matches, load_plan
from twinkly_inspector import save_json


def member_map(snapshot, plan):
    """Match cloud object membership by hardware MAC, not object IDs/names."""
    identities = [mac(snapshot[FILES[i + 1]]["gestalt"].get("mac")) for i in range(2)]
    if not all(identities) or identities[0] == identities[1]:
        raise ValueError("Cannot verify both unique controller MAC addresses")
    return identities


def matched_group(snapshot, identities):
    matched = []
    for obj in objects_from(snapshot[FILES[0]]):
        devices = obj.get("devices")
        if not isinstance(devices, list):
            continue
        found = [mac(slot.get("device", {}).get("mac"))
                 for slot in devices if isinstance(slot, dict)
                 and isinstance(slot.get("device"), dict)]
        if set(identities).intersection(found):
            matched.append((obj, found))
    if len(matched) != 1 or matched[0][1] != identities:
        raise ValueError("Cloud group membership is ambiguous or out of order")
    return matched[0][0]


def validate_checkpoint(report, expected):
    if report.get("result") != "PASS_READ_ONLY" or report.get("state") != expected:
        raise RuntimeError("Checkpoint failed: expected " + expected +
                           ", observed " + str(report.get("state")) +
                           "; " + "; ".join(report.get("issues", [])))
    if expected == "grouped-cloud":
        if not report.get("mapping", {}).get("matching"):
            raise RuntimeError("Grouped cloud/local geometry does not match")
    if expected == "ungrouped-cloud":
        individuals = report.get("individual_mappings", {})
        if set(individuals) != {"Left", "Right"} or not all(
                item.get("matching") for item in individuals.values()):
            raise RuntimeError("Individual cloud/local layouts do not match")


def check_standalone_maps(snapshot, plan):
    for index, member in enumerate(plan["members"]):
        current = snapshot[FILES[index + 1]]["layout"]
        reference = member["standalone_layout"]
        if not layout_matches(current, reference):
            raise RuntimeError(member["name"] +
                               ": standalone 3D layout does not match private backup")
    print("Standalone 3D mapping backups verified on both controllers.")


def check_returned_map(before, after, plan):
    for index, member in enumerate(plan["members"]):
        first = before[FILES[index + 1]]["layout"]
        last = after[FILES[index + 1]]["layout"]
        if first.get("source") != last.get("source"):
            raise RuntimeError(member["name"] + ": layout source changed")
        comparison = compare_points(first.get("coordinates", []),
                                    last.get("coordinates", []))
        if not comparison["matching"]:
            raise RuntimeError(member["name"] +
                               ": grouped layout changed: " + json.dumps(comparison))
    first_cloud = matched_group(before, member_map(before, plan))
    last_cloud = matched_group(after, member_map(after, plan))
    cloud_comp = compare_points(coordinates(first_cloud["layout"]),
                                coordinates(last_cloud["layout"]))
    if not cloud_comp["matching"]:
        raise RuntimeError("Cloud grouped geometry changed: " + json.dumps(cloud_comp))
    print("Final Area geometry agrees with initial Area geometry (all LEDs).")


def checkpoint(label, plan_path, expected, timeout, output, session):
    print("\n" + "=" * 55)
    print(label + ": LIVE read-only cloud + LAN verification")
    print("=" * 55)
    report, plan, snapshot = collect(plan_path, timeout=timeout, output=output,
                                     include_snapshot=True)
    stage = {
        "label": label,
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "result": report.get("result"),
        "state": report.get("state"),
        "object_ids": [x.get("id") for x in report.get("matched_objects", [])],
        "issues": report.get("issues", []),
    }
    session["checkpoints"].append(stage)
    validate_checkpoint(report, expected)
    print(label + ": PASS", expected)
    return plan, snapshot


def require_app_action(description):
    print("\nACTION REQUIRED IN TWINKLY APP:")
    print(description)
    print("Do not modify controller mappings manually. Return here after the app finishes.")
    response = input("Type CHECK to verify the new state, or CANCEL to stop: ").strip()
    if response != "CHECK":
        raise RuntimeError("User cancelled. No further stages attempted.")


def run(args):
    if not 0 < args.timeout <= 30:
        raise ValueError("timeout must be between 0 and 30 seconds")
    plan, _ = load_plan(args.plan)
    if len(plan["members"]) != 2:
        raise ValueError("Only two-controller Area groups are supported")
    # A renamed cloud Area is fine; names are descriptive, never device identity.
    if args.group.casefold() != plan["group_name"].casefold():
        print("Using group label", repr(args.group),
              "(cloud membership will be matched by MAC, not by this label)")
    print("Sequence: Check grouped -> app ungroup -> Check standalone -> "
          "app regroup -> Check grouped")
    print("Only reads and local snapshot saves are made by this script.")
    if not args.execute:
        print("DRY RUN: no app actions requested and no network requests made.")
        print("Run again with --execute to start the interactive five-stage audit.")
        return 0
    if not args.acknowledge_risk or args.group_confirm != args.group:
        raise ValueError("Run with --acknowledge-risk and --group-confirm matching --group")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    private = Path(args.output) / ("roundtrip-" + stamp)
    private.mkdir(parents=True, exist_ok=False)
    session = {"workflow": "app-assisted-group-roundtrip", "group_label": args.group,
               "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "checkpoints": [], "complete": False}
    try:
        first_plan, first = checkpoint("1. Check BEFORE ungroup",
                                       args.plan, "grouped-cloud",
                                       args.timeout, private, session)
        member_map(first, first_plan)
        require_app_action("UNGROUP the Office Area in the Twinkly app. "
                           "Wait for two separate Office lights to appear.")
        second_plan, second = checkpoint("3. Check AFTER ungroup",
                                         args.plan, "ungrouped-cloud",
                                         args.timeout, private, session)
        if member_map(first, first_plan) != member_map(second, second_plan):
            raise RuntimeError("Controller identity changed after ungroup")
        check_standalone_maps(second, second_plan)
        require_app_action("GROUP the same two Office controllers in the Twinkly app, "
                           "with Office Left first and Office Right second. "
                           "Wait for the Area to finish creating.")
        third_plan, third = checkpoint("5. Check AFTER regroup",
                                      args.plan, "grouped-cloud",
                                      args.timeout, private, session)
        if member_map(first, first_plan) != member_map(third, third_plan):
            raise RuntimeError("Controller identity changed after regroup")
        check_returned_map(first, third, third_plan)
        session["complete"] = True
        print("\nROUND TRIP VERIFIED: grouped -> ungrouped -> grouped.")
        print("All three cloud/LAN checkpoints passed; original mappings preserved.")
        print("Confirm playback on both windows in the Twinkly app.")
        return 0
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        session["error"] = str(exc)
        print("\nSTOPPED:", exc, file=sys.stderr)
        print("The script never attempts an automatic rollback or retry.", file=sys.stderr)
        print("The Twinkly app may now be in an intermediate state.", file=sys.stderr)
        return 2
    finally:
        save_json(private / "roundtrip-status-private.json", session)
        print("Private snapshot/report folder:", private)
        print("Do NOT commit or publicly upload these cloud controller backups.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, help="Saved or app-visible group label")
    parser.add_argument("--plan", default="private-area-plan.json")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--output", default="cloud-preflight-backups")
    parser.add_argument("--execute", action="store_true",
                        help="Start app-assisted round trip; requires confirmations")
    parser.add_argument("--acknowledge-risk", action="store_true")
    parser.add_argument("--group-confirm", default="")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
