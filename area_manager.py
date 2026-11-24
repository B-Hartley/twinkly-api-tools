#!/usr/bin/env python3
"""Twinkly Area manager: independent capture, group, ungroup and verify operations.

Cloud/LAN group writers are the proven, guarded cloud_group_cycle primitives;
NOT an untested alternative protocol implementation. Dry-run by default.
Requires private plan, private Windows cloud token, and local 2D/3D archives.
"""
import argparse
from pathlib import Path
import sys

import archive_2d_group as groups
import archive_3d_maps as maps
import cloud_group_cycle as cycle
import cloud_group_cycle_reverse as reverse
from cloud_group_preflight import FILES, objects_from, mac
from cloud_group_live import collect
from group_test import load_plan
from twinkly_inspector import save_json


def execute(args):
    if args.command == "capture-3d":
        if not args.devices:
            count = maps.from_plan(args.plan, args.root or "3d-map-archive",
                                   allow_saved_plan=args.allow_saved_plan,
                                   timeout=args.timeout)
            print("3D references created:", count)
        else:
            count, skipped = maps.from_devices(args.devices, args.root or "3d-map-archive",
                                               timeout=args.timeout, names=args.names)
            print("3D references created:", count, "; skipped:", skipped)
        return 0
    if args.command == "capture-group":
        report, plan, snapshot = collect(args.plan, timeout=args.timeout,
                                        output=args.output, include_snapshot=True)
        if report["result"] != "PASS_READ_ONLY" or report["state"] != "grouped-cloud":
            raise ValueError("A verified grouped cloud/LAN reference is required")
        archive = groups.archive(snapshot, root=args.root or "2d_group_archive",
                                 source="live-group-cloud-preflight")
        print("Captured immutable 2D group:", archive)
        return 0
    if args.command == "verify":
        report, plan, snapshot = collect(args.plan, timeout=args.timeout,
                                        output=args.output, include_snapshot=True)
        if report["result"] != "PASS_READ_ONLY":
            raise ValueError("Live cloud/LAN verification blocked: " + str(report["issues"]))
        print("VERIFY PASS:", report["state"])
        from movie_retention import capture
        members = capture(plan, Path(args.output) / "verify-movie-catalogues",
                          "read-only", args.timeout, save_json)
        if report["state"] == "grouped-cloud":
            obj = cycle.find_area(snapshot, plan)
            print("Cloud Area:", obj.get("name"), obj.get("id"))
        else:
            print("Standalone state; cloud/local group check passed")
        return 0
    if args.command in ("group", "ungroup"):
        if not args.execute:
            print("DRY RUN: performs only read-only cloud/LAN preflight.")
        options = argparse.Namespace(
            group=args.group, plan=args.plan, output=args.output, timeout=args.timeout,
            execute=args.execute, acknowledge_risk=args.acknowledge_risk,
            confirm_group=args.confirm_group, check_movies=args.check_movies,
            start_ungrouped=args.command == "group",
            stop_after_group=args.command == "group",
            stop_after_ungroup=args.command == "ungroup",
        )
        return reverse.run_reverse(options) if args.command == "group" else cycle.run(options)
    raise ValueError("Unsupported operation")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("capture-group", "capture-3d", "ungroup", "group", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--plan", default="private-area-plan.json")
        sp.add_argument("--timeout", type=float, default=10)
        sp.add_argument("--output", default="cloud-preflight-backups")
        if name.startswith("capture"):
            sp.add_argument("--root", help="Archive directory; private and gitignored")
        if name == "capture-3d":
            sp.add_argument("--devices", help="Private devices.json for standalone capture")
            sp.add_argument("--names", nargs="+", help="Only these device names")
            sp.add_argument("--allow-saved-plan", action="store_true",
                            help="Use independently verified saved 3D map if group is currently 2D")
        if name in ("group", "ungroup"):
            sp.add_argument("--group", required=True)
            sp.add_argument("--check-movies", action="store_true")
            sp.add_argument("--execute", action="store_true")
            sp.add_argument("--acknowledge-risk", action="store_true")
            sp.add_argument("--confirm-group")
    args = p.parse_args(argv)
    if not 0.1 <= args.timeout <= 30:
        p.error("--timeout must be between 0.1 and 30")
    try:
        return execute(args)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
