#!/usr/bin/env python3
"""Privately archive a fully verified grouped 2D Twinkly Area snapshot.

Read-only: uses prior saved cloud+LAN preflight JSON, never contacts devices,
never changes a controller/cloud group. Archive files are create-only.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

from cloud_group_preflight import (
    FILES, CLOUD_LOCAL_TOLERANCE, compare_points, coordinates, evaluate, mac, normalized_uid, objects_from,
    read_stage,
)

DEFAULT_ROOT = "2d_group_archive"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def verify(snapshot):
    report = evaluate(snapshot)
    if report["result"] != "PASS_READ_ONLY" or report["state"] != "grouped-cloud":
        raise ValueError("Snapshot is not a fully verified cloud+LAN grouped state")
    macs = [mac(snapshot[FILES[i + 1]]["gestalt"]["mac"]) for i in range(2)]
    if None in macs or macs[0] == macs[1]:
        raise ValueError("Invalid controller MAC identities")
    obj_matches = [
        obj for obj in objects_from(snapshot[FILES[0]])
        if isinstance(obj.get("devices"), list) and
        [mac(slot.get("device", {}).get("mac")) for slot in obj["devices"]] == macs
    ]
    if len(obj_matches) != 1:
        raise ValueError("Cloud Area not uniquely identified")
    obj = obj_matches[0]
    uid = normalized_uid(snapshot[FILES[1]]["group"]["group"].get("uid"))
    if not uid or normalized_uid(obj.get("uuid")) != uid:
        raise ValueError("Cloud Area UUID differs from paired local group UUID")
    if obj.get("ledCount") != sum(
            snapshot[FILES[i + 1]]["gestalt"]["number_of_led"] for i in range(2)):
        raise ValueError("Cloud/locals LED count mismatch")
    layout = obj.get("layout")
    needed = ("coords", "uuid", "geometry", "aspectXZ",
              "aspectXY", "name", "synthesized")
    if not isinstance(layout, dict) or any(key not in layout for key in needed):
        raise ValueError("Cloud Area missing create metadata")
    if any(k not in obj for k in ("capacity", "fps", "ledProfile", "meta", "uuid")):
        raise ValueError("Cloud Area missing object metadata")
    layouts = []
    for i in range(2):
        local = snapshot[FILES[i + 1]]["layout"]
        if local.get("source") != "2d" or not isinstance(local.get("synthesized"), bool):
            raise ValueError("Local grouped 2D layout incomplete")
        layouts.append({k: local[k] for k in ("source", "synthesized", "coordinates")})
    if not compare_points(coordinates(layout), layouts[0]["coordinates"] +
                          layouts[1]["coordinates"], tolerance=CLOUD_LOCAL_TOLERANCE)["matching"]:
        raise ValueError("Cloud Area 2D coordinates differ from local group")
    return obj, layouts, macs


def archive(snapshot, root=DEFAULT_ROOT, source="offline-preflight-snapshot"):
    obj, layouts, macs = verify(snapshot)
    key = "_".join(macs)
    # Each distinct Area UUID is archived independently. Previously saved
    # versions are never overwritten, even when the group name is reused.
    uid = normalized_uid(obj["uuid"])
    destination = Path(root) / key / uid
    files = {
        "area-cloud-private.json": obj,
        "office-left-layout-private.json": layouts[0],
        "office-right-layout-private.json": layouts[1],
        "group-local-left-private.json": snapshot[FILES[1]]["group"],
        "group-local-right-private.json": snapshot[FILES[2]]["group"],
    }
    manifest = {
        "schema": 1,
        "captured_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": source,
        "cloud_area_id": obj.get("id"),
        "group_name": obj.get("name"),
        "group_uuid": uid,
        "macs_in_order": macs,
        "led_count": obj["ledCount"],
        "sha256": {name: checksum(payload) for name, payload in files.items()},
        "note": "Private immutable 2D group reference; no writes to Twinkly devices",
    }
    if destination.exists():
        existing = read_archive(destination)
        if existing["manifest"]["sha256"] != manifest["sha256"]:
            raise ValueError("Archived grouped reference already exists with changed data; refusing overwrite")
        print("Existing 2D reference verified; unchanged:", destination)
        return destination
    destination.mkdir(parents=True, exist_ok=False)
    # Exclusive creates; separate immutable content and manifest checksums.
    for name, payload in files.items():
        with (destination / name).open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
    with (destination / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False, sort_keys=True)
    print("Archived verified 2D cloud Area and both local layouts:", destination)
    return destination


def read_archive(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported 2D archive schema")
    expected = manifest.get("sha256")
    if not isinstance(expected, dict) or set(expected) != {
            "area-cloud-private.json", "office-left-layout-private.json",
            "office-right-layout-private.json", "group-local-left-private.json",
            "group-local-right-private.json"}:
        raise ValueError("Incomplete 2D archive")
    payloads = {}
    for name, digest in expected.items():
        path = folder / name
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Missing/oversized 2D reference file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if checksum(payload) != digest:
            raise ValueError("2D archive checksum mismatch: " + name)
        payloads[name] = payload
    obj = payloads["area-cloud-private.json"]
    layouts = [payloads["office-left-layout-private.json"],
               payloads["office-right-layout-private.json"]]
    ids = manifest["macs_in_order"]
    if [mac(s.get("device", {}).get("mac")) for s in obj["devices"]] != ids:
        raise ValueError("2D archive device identities changed")
    if normalized_uid(obj.get("uuid")) != manifest["group_uuid"]:
        raise ValueError("2D cloud group UUID changed")
    if obj.get("ledCount") != manifest["led_count"]:
        raise ValueError("2D archive LED count changed")
    if not compare_points(coordinates(obj["layout"]), layouts[0]["coordinates"] +
                          layouts[1]["coordinates"], tolerance=CLOUD_LOCAL_TOLERANCE)["matching"]:
        raise ValueError("2D archive cloud/local geometry no longer agrees")
    return {"manifest": manifest, "area": obj, "layouts": layouts}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot-dir", required=True,
                   help="Fully verified cloud-preflight-backups/YYYY... folder")
    p.add_argument("--root", default=DEFAULT_ROOT)
    args = p.parse_args(argv)
    try:
        snapshot = read_stage(directory=Path(args.snapshot_dir))
        archive(snapshot, args.root, source=str(args.snapshot_dir))
        print("No network or controller/cloud writes.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
