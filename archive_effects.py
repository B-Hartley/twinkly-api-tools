#!/usr/bin/env python3
"""Create a private, immutable, metadata-only Twinkly effect reference.

Input: one twinkly_inspector.py inspect snapshot folder. This does NOT
download actual movie frames, and cannot reconstruct custom movies.
No authentication, network or controller/cloud writes.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

from twinkly_inspector import canonical, scrub

ROOT = "effects-archive"
LABELS = (
    "gestalt", "led_mode", "led_movie_config", "current_effect",
    "effects_catalogue", "movies_catalogue", "movies_current", "playlist",
    "playlist_current",
)


def checksum(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_snapshot(path):
    folder = Path(path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    controllers = manifest.get("controllers")
    if not isinstance(controllers, list) or not controllers:
        raise ValueError("No controllers in inspector snapshot")
    data = []
    for c in controllers:
        if c.get("status") != "inspected":
            raise ValueError("All controllers must have been inspected")
        ip = c.get("ip", "")
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip):
            raise ValueError("Invalid controller IP in snapshot")
        folder_responses = folder / "controllers" / ip.replace(".", "_") / "responses"
        responses = {}
        for label in LABELS:
            item = folder_responses / (label + ".json")
            if item.is_file():
                if item.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Oversized endpoint capture: " + label)
                responses[label] = scrub(json.loads(item.read_text(encoding="utf-8")))
            else:
                responses[label] = {"ok": False, "error": "Not captured in this inspector version"}
        identity = responses["gestalt"].get("data", {})
        if not responses["gestalt"].get("ok") or not isinstance(identity, dict):
            raise ValueError("Missing controller hardware identity")
        mac = identity.get("mac")
        if not isinstance(mac, str) or not re.fullmatch(
                r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}|[0-9a-fA-F]{12}", mac):
            raise ValueError("Invalid controller MAC")
        data.append({"mac": mac.replace(":", "").lower(),
                     "name": c.get("name", ""), "responses": responses})
    macs = [c["mac"] for c in data]
    if len(set(macs)) != len(macs):
        raise ValueError("Duplicate controller MAC")
    return manifest, data


def archive(snapshot, root=ROOT):
    manifest, devices = load_snapshot(snapshot)
    # Hash of the whole metadata reference creates a content-addressed, immutable
    # archive. Different movie selections/catalogues have different directories.
    payload = {"schema": 1,
               "source_snapshot": str(snapshot),
               "captured_utc": manifest.get("captured_utc"),
               "controllers": devices,
               "limitations": {
                   "raw_movie_frames_captured": False,
                   "fully_restorable_movie_backup": False,
                   "reason": "Documented Twinkly movie APIs support upload but no confirmed read-only frame download.",
               }}
    digest = checksum(payload)
    mac_key = "_".join(c["mac"] for c in devices)
    destination = Path(root) / mac_key / digest[:24]
    if destination.exists():
        saved = json.loads((destination / "reference.json").read_text(encoding="utf-8"))
        if checksum(saved) != digest:
            raise ValueError("Archive collision or existing reference modified")
        print("Existing immutable effect metadata verified:", destination)
        return destination
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "reference.json").open("x", encoding="utf-8") as output:
        json.dump(payload, output, indent=2, sort_keys=True, ensure_ascii=False)
    with (destination / "sha256.txt").open("x", encoding="ascii") as output:
        output.write(digest + "\n")
    print("Archived effect metadata:", destination)
    for c in devices:
        responses = c["responses"]
        catalogue = responses["movies_catalogue"]
        items = catalogue.get("data", {}).get("movies") if catalogue.get("ok") else None
        names = [m.get("name", "<unnamed>") for m in items if isinstance(m, dict)] if isinstance(items, list) else []
        print(" ", c["name"], "movies:", ", ".join(names) if names else
              ("none" if isinstance(items, list) else "catalogue unavailable"))
    print("WARNING: actual movie frames NOT captured; this is NOT a restorable custom-effect backup.")
    print("No device, cloud or network writes performed.")
    return destination


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot-dir", required=True, type=Path)
    p.add_argument("--root", default=ROOT)
    args = p.parse_args(argv)
    try:
        archive(args.snapshot_dir, args.root)
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
