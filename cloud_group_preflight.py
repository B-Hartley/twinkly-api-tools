#!/usr/bin/env python3
"""Read-only, offline Twinkly cloud/local Area preflight.

Accepts a stage directory or a ZIP stage prefix containing:
cloud-objects-PRIVATE.json, Office_Left-local-PRIVATE.json,
Office_Right-local-PRIVATE.json. Uses MAC addresses only in memory to
match devices. Refuses any discrepancy, ambiguity, or partial group.
Does not authenticate, connect to a network or modify controllers/cloud.
"""
import argparse
import base64
import gzip
import io
import json
import math
from pathlib import Path
import sys
import zipfile

TOLERANCE = 0.0002
MAX_POINTS = 100000
FILES = ("cloud-objects-PRIVATE.json", "Office_Left-local-PRIVATE.json",
         "Office_Right-local-PRIVATE.json")


def mac(value):
    if not isinstance(value, str):
        return None
    clean = "".join(c for c in value.lower() if c in "0123456789abcdef")
    return clean if len(clean) == 12 and len(value.replace(":", "").replace("-", "")) == 12 else None


def read_stage(directory=None, archive=None, stage=None):
    if archive is not None:
        if not stage or stage.startswith("/") or ".." in stage.split("/"):
            raise ValueError("ZIP stage must be an explicit safe relative directory")
        with zipfile.ZipFile(archive) as z:
            names = set(z.namelist())
            prefix = stage.strip("/") + "/"
            result = {}
            for filename in FILES:
                member = prefix + filename
                if member not in names:
                    raise ValueError("Stage missing expected file: " + filename)
                if z.getinfo(member).file_size > 32 * 1024 * 1024:
                    raise ValueError("Input snapshot exceeds 32 MB")
                result[filename] = json.loads(z.read(member))
            return result
    if stage:
        raise ValueError("--stage is only valid with --archive")
    if directory is None:
        raise ValueError("Supply --snapshot-dir or --archive and --stage")
    result = {}
    for filename in FILES:
        source = Path(directory) / filename
        if not source.is_file() or source.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Missing/oversized snapshot: " + filename)
        result[filename] = json.loads(source.read_text(encoding="utf-8"))
    return result


def objects_from(snapshot):
    if isinstance(snapshot, list):
        return snapshot
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("objects"), list):
        raise ValueError("Unknown cloud object inventory schema")
    entries = snapshot["objects"]
    if isinstance(snapshot.get("count"), int) and snapshot["count"] != len(entries):
        raise ValueError("Incomplete cloud inventory")
    if snapshot.get("next_page"):
        raise ValueError("Cloud inventory has unvisited pages")
    return entries


def coordinates(layout):
    if not isinstance(layout, dict) or not isinstance(layout.get("coords"), str):
        raise ValueError("Missing encoded cloud layout")
    compressed = base64.b64decode(layout["coords"], validate=True)
    if len(compressed) > 16 * 1024 * 1024:
        raise ValueError("Compressed mapping too large")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as g:
        body = g.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("Uncompressed mapping too large")
    points = json.loads(body)
    if not isinstance(points, list) or len(points) > MAX_POINTS:
        raise ValueError("Invalid coordinate array")
    return points


def compare_points(cloud, local, tolerance=TOLERANCE):
    if len(cloud) != len(local):
        return {"matching": False, "expected": len(local), "actual": len(cloud),
                "mismatched_leds": abs(len(cloud) - len(local)),
                "max_delta": None, "examples": []}
    failures = []
    max_delta = 0.0
    for index, (c, l) in enumerate(zip(cloud, local)):
        try:
            delta = max(abs(float(c[axis]) - float(l[axis])) for axis in ("x", "y", "z"))
            if not math.isfinite(delta):
                raise ValueError("Non-finite point")
        except (ValueError, TypeError, KeyError, OverflowError):
            delta = None
        if delta is None or delta > tolerance:
            failures.append({"index_zero_based": index, "index_one_based": index + 1,
                             "max_delta": delta})
        if delta is not None:
            max_delta = max(max_delta, delta)
    return {"matching": not failures, "expected": len(local), "actual": len(cloud),
            "mismatched_leds": len(failures), "max_delta": max_delta,
            "examples": failures[:10]}


def evaluate(snapshot):
    problems = []
    members = []
    for label, filename in (("Left", FILES[1]), ("Right", FILES[2])):
        data = snapshot[filename]
        gestalt, group, layout = (data.get("gestalt"), data.get("group"), data.get("layout"))
        if not all(isinstance(x, dict) for x in (gestalt, group, layout)):
            raise ValueError(label + ": invalid local snapshot")
        address = mac(gestalt.get("mac"))
        n = gestalt.get("number_of_led")
        points = layout.get("coordinates")
        if not address or type(n) is not int or n <= 0 or not isinstance(points, list) or len(points) != n:
            raise ValueError(label + ": invalid device identity or LED coordinates")
        role = group.get("group")
        if not isinstance(role, dict):
            raise ValueError(label + ": missing local group response")
        members.append({"label": label, "mac": address, "leds": n, "points": points,
                        "role": role, "source": layout.get("source")})
    if members[0]["mac"] == members[1]["mac"]:
        raise ValueError("Duplicate local hardware identity")
    expected_set = {m["mac"] for m in members}
    selected = []
    for obj in objects_from(snapshot[FILES[0]]):
        if not isinstance(obj, dict):
            raise ValueError("Unexpected cloud object record")
        slots = obj.get("devices")
        if not isinstance(slots, list):
            continue
        ids = []
        for slot in slots:
            if not isinstance(slot, dict) or not isinstance(slot.get("device"), dict):
                continue
            addr = mac(slot["device"].get("mac"))
            if addr:
                ids.append(addr)
        if expected_set.intersection(ids):
            selected.append((obj, ids))
    output = {"result": "BLOCK", "state": "unknown", "matched_objects": [],
              "issues": problems, "mapping": None}
    if not selected:
        problems.append("No cloud objects contain either intended controller MAC")
        return output
    for obj, ids in selected:
        output["matched_objects"].append({
            "id": obj.get("id"), "name": obj.get("name"),
            "led_count": obj.get("ledCount"), "member_count": len(ids)})
    if len(selected) == 1 and len(selected[0][1]) == 2 and set(selected[0][1]) == expected_set:
        obj, ids = selected[0]
        slots = obj["devices"]
        if ids != [m["mac"] for m in members]:
            problems.append("Cloud member order differs from expected Left, Right")
        if len(slots) != 2 or [x.get("part") for x in slots] != [0, 1]:
            problems.append("Cloud group part values must be exactly 0 and 1")
        if obj.get("ledCount") != sum(m["leds"] for m in members):
            problems.append("Cloud group LED count differs from local total")
        output["state"] = "grouped-cloud"
        cloud_group = [m["role"] for m in members]
        uids = [x.get("uid") for x in cloud_group]
        if cloud_group[0].get("mode") != "master" or cloud_group[1].get("mode") != "slave":
            problems.append("Local roles are not master/slave")
        if not uids[0] or uids[0] != uids[1]:
            problems.append("Local group UUIDs missing or different")
        if cloud_group[1].get("offset") != members[0]["leds"]:
            problems.append("Slave offset differs from master LED count")
        if any(x.get("size") != obj.get("ledCount") for x in cloud_group):
            problems.append("Local total group size differs from cloud")
        if any(m["source"] != (obj.get("layout") or {}).get("source")
               for m in members):
            problems.append("Cloud/local layout source differs")
        try:
            cloud_points = coordinates(obj.get("layout"))
            ordered_local = members[0]["points"] + members[1]["points"]
            comparison = compare_points(cloud_points, ordered_local)
            output["mapping"] = comparison
            if not comparison["matching"]:
                problems.append("Cloud/local mapping differs beyond tolerance")
        except (ValueError, OSError, EOFError, json.JSONDecodeError) as exc:
            problems.append("Cannot validate combined cloud layout: " + type(exc).__name__)
    elif len(selected) == 2 and all(len(ids) == 1 for _, ids in selected) and {
            ids[0] for _, ids in selected} == expected_set:
        output["state"] = "ungrouped-cloud"
        for member in members:
            if member["role"].get("mode") != "none":
                problems.append(member["label"] + ": local controller is not ungrouped")
            obj = next(obj for obj, ids in selected if ids == [member["mac"]])
            if obj.get("ledCount") != member["leds"]:
                problems.append(member["label"] + ": cloud LED count differs")
            if obj.get("layout", {}).get("source") != member["source"]:
                problems.append(member["label"] + ": cloud/local source differs")
            try:
                comparison = compare_points(coordinates(obj.get("layout")), member["points"])
                output.setdefault("individual_mappings", {})[member["label"]] = comparison
                if not comparison["matching"]:
                    problems.append(member["label"] + ": cloud/local mapping differs")
            except (ValueError, OSError, EOFError, json.JSONDecodeError) as exc:
                problems.append(member["label"] + ": cannot validate cloud layout: " + type(exc).__name__)
    else:
        problems.append("Cloud membership ambiguous, duplicated or partially grouped")
    if not problems:
        output["result"] = "PASS_READ_ONLY"
    return output


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot-dir", type=Path)
    source.add_argument("--archive", type=Path)
    p.add_argument("--stage", help="Path inside ZIP, e.g. office-join-cycle/DATE/02-joined")
    p.add_argument("--output", type=Path, help="Optional private JSON report (never credentials)")
    args = p.parse_args(argv)
    try:
        report = evaluate(read_stage(args.snapshot_dir, args.archive, args.stage))
        print("Twinkly cloud/local preflight:", report["result"], "state:", report["state"])
        for obj in report["matched_objects"]:
            print("  Cloud object", obj["id"], repr(obj["name"]),
                  "LEDs", obj["led_count"], "members", obj["member_count"])
        for issue in report["issues"]:
            print("  BLOCK:", issue)
        if report["mapping"]:
            print("  Combined mapping:", json.dumps(report["mapping"]))
        for label, comparison in report.get("individual_mappings", {}).items():
            print("  " + label + " mapping:", json.dumps(comparison))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("NO WRITES: this command used saved snapshot files only.")
        return 0 if report["result"] == "PASS_READ_ONLY" else 2
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile,
            json.JSONDecodeError) as exc:
        print("BLOCK:", type(exc).__name__, str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
