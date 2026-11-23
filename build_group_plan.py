#!/usr/bin/env python3
"""Create a guarded group-test plan using two inspector snapshots (no network writes)."""
import argparse
import json
from pathlib import Path
import sys
import uuid
from twinkly_inspector import save_json


def load_controller(root, controller):
    ip = controller["ip"]
    folder = root / "controllers" / ip.replace(".", "_") / "responses"
    def read(name):
        response = json.loads((folder / (name + ".json")).read_text(encoding="utf-8"))
        if not response.get("ok"):
            raise ValueError(f"{ip}: missing successful {name} in {root}")
        return response["data"]
    return read("led_layout_full"), read("summary")


def group_fields(summary):
    if isinstance(summary, dict):
        for candidate in (summary.get("group"), summary.get("application", {}).get("group")
                          if isinstance(summary.get("application"), dict) else None):
            if isinstance(candidate, dict) and candidate.get("mode") in ("master", "slave"):
                return candidate
    raise ValueError("Could not locate grouping metadata in summary; inspect raw JSON manually")


def select_members(controllers, requested):
    """Match human-friendly names (case/space insensitive) or exact IPs."""
    choices = list(controllers.values())
    selected = []
    for query in requested:
        normalized = " ".join(query.split()).casefold()
        found = [c for c in choices if c["ip"] == query or
                 " ".join(str(c.get("name", "")).split()).casefold() == normalized]
        if len(found) != 1:
            available = ", ".join(f"{c.get('name', '?')} ({c['ip']})" for c in choices)
            raise ValueError(
                f"Member {query!r} matched {len(found)} controllers. "
                f"Grouped snapshot contains: {available}")
        if found[0]["ip"] in {c["ip"] for c in selected}:
            raise ValueError(f"Duplicate member requested: {query}")
        selected.append(found[0])
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grouped", required=True, help="Inspector snapshot while grouped")
    parser.add_argument("--standalone", required=True, help="Inspector snapshot after ungroup")
    parser.add_argument("--members", nargs="+", required=True, help="Names or IPs of group members")
    parser.add_argument("--name", required=True, help="Local friendly name")
    parser.add_argument("--out", default="private-area-plan.json")
    a = parser.parse_args()
    try:
        grouped, standalone = Path(a.grouped), Path(a.standalone)
        g = json.loads((grouped / "manifest.json").read_text(encoding="utf-8"))
        s = json.loads((standalone / "manifest.json").read_text(encoding="utf-8"))
        gc = {c["ip"]: c for c in g["controllers"]}
        sc = {c["ip"]: c for c in s["controllers"]}
        matches = select_members(gc, a.members)
        members, uid, size = [], None, None
        for c in matches:
            ip = c["ip"]
            if ip not in sc:
                raise ValueError(f"{ip}: absent from standalone snapshot")
            area, summary = load_controller(grouped, c)
            individual, _ = load_controller(standalone, sc[ip])
            group = group_fields(summary)
            if uid is None:
                uid, size = group.get("uid"), group.get("size")
            if str(group.get("uid", "")).lower() != str(uid).lower() or group.get("size") != size:
                raise ValueError("Group UID / total size inconsistent")
            if area.get("source") != "2d" or individual.get("source") != "3d":
                raise ValueError(f"{ip}: expected Area 2D and standalone 3D camera mappings")
            n = len(area.get("coordinates", []))
            if n == 0 or len(individual.get("coordinates", [])) != n:
                raise ValueError(f"{ip}: coordinate counts disagree")
            members.append({"ip": ip, "name": c["name"], "offset": group.get("offset", 0), "leds": n,
                            "area_layout": {k: area[k] for k in ("source", "synthesized", "coordinates")},
                            "standalone_layout": {k: individual[k] for k in ("source", "synthesized", "coordinates")}})
        members.sort(key=lambda item: item["offset"])
        if sum(x["leds"] for x in members) != size:
            raise ValueError("The selected members do not account for the whole group")
        plan = {"schema": 1, "group_name": a.name, "uid": str(uuid.UUID(uid)),
                "size": size, "preverified_backup": False, "members": members,
                "warning": "Inspect both coordinate sets and controller identities, then set preverified_backup=true locally."}
        save_json(Path(a.out), plan)
        print(f"Created {a.out}; {len(members)} members, {size} LEDs")
        print("Plan is NOT live-write ready until manual verification and explicit acknowledgement.")
        return 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
