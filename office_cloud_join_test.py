#!/usr/bin/env python3
"""Read-only observation of Office join -> unjoin in Twinkly app.

The test writes no grouping or layout changes. LAN login/verify POSTs are
necessary to read the local API. You perform join/unjoin in the app.
Cloud and local raw snapshots contain secrets: keep output private.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error

from cloud_audit import cloud_get, objects_from, decode_coords
from cloud_token_store import access_token as cached_access_token
from group_test import load_plan, layout_matches
from twinkly_inspector import authenticate, request, save_json

SELECTOR = "/v3/objects?mine=true&fields=layout,devices,capabilities"
INSTALLS = "/v3/installations"
LAN_ENDPOINTS = (("group", "/xled/v1/application/group"),
                 ("layout", "/xled/v1/led/layout/full"),
                 ("gestalt", "/xled/v1/gestalt"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                  separators=(",", ":")).encode()).hexdigest()[:16]


def capture(plan, token, timeout, folder):
    folder.mkdir(parents=True, exist_ok=False)
    objects_reply = cloud_get(SELECTOR, token, timeout)
    installations_reply = cloud_get(INSTALLS, token, timeout)
    save_json(folder / "cloud-objects-PRIVATE.json", objects_reply)
    save_json(folder / "cloud-installations-PRIVATE.json", installations_reply)
    objects = objects_from(objects_reply)
    cloud = []
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError("Invalid object response")
        layout = obj.get("layout")
        summary = {"id": obj.get("id"), "name": obj.get("name"),
                   "leds": obj.get("ledCount"),
                   "devices": len(obj["devices"]) if isinstance(obj.get("devices"), list) else None,
                   "source": layout.get("source") if isinstance(layout, dict) else None,
                   "coordinates": None, "fingerprint": None}
        if isinstance(layout, dict) and isinstance(layout.get("coords"), str):
            coords = decode_coords(layout["coords"])
            summary["coordinates"] = len(coords)
            summary["fingerprint"] = digest(coords)
        cloud.append(summary)
    local = []
    for m in plan["members"]:
        token_lan, _ = authenticate(m["ip"], timeout)
        if not token_lan:
            raise RuntimeError(f"{m['name']}: local login failed")
        replies = {}
        for label, path in LAN_ENDPOINTS:
            response = request(m["ip"], path, token=token_lan, timeout=timeout)
            if not response.get("ok") or not isinstance(response.get("data"), dict):
                raise RuntimeError(f"{m['name']}: local {label} read failed")
            replies[label] = response["data"]
        save_json(folder / (m["name"].replace(" ", "_") + "-local-PRIVATE.json"), replies)
        group = replies["group"].get("group")
        layout = replies["layout"]
        coords = layout.get("coordinates")
        if not isinstance(group, dict) or not isinstance(coords, list):
            raise ValueError(f"{m['name']}: invalid controller response")
        if len(coords) != m["leds"] or replies["gestalt"].get("number_of_led") != m["leds"]:
            raise ValueError(f"{m['name']}: controller LED count mismatch")
        expected_role = "master" if m["offset"] == 0 else "slave"
        is_joined = (group.get("mode") == expected_role and
                     bool(group.get("uid")) and
                     group.get("size") == plan["size"] and
                     group.get("offset", 0) == m["offset"])
        state = "joined" if is_joined else ("unjoined" if group.get("mode") == "none" else "unexpected")
        local.append({"name": m["name"], "state": state,
                      "group_uid": group.get("uid"),
                      "source": layout.get("source"), "synthesized": layout.get("synthesized"),
                      "layout_uuid": layout.get("uuid"), "leds": len(coords),
                      "fingerprint": digest(coords),
                      "matches_saved_standalone": layout_matches(layout, m["standalone_layout"]),
                      "matches_saved_area": layout_matches(layout, m["area_layout"])})
    summary = {"local": local, "cloud": cloud, "cloud_count": len(cloud)}
    save_json(folder / "summary-PRIVATE.json", summary)
    return summary


def validate_phase(snapshot, phase):
    local = snapshot["local"]
    wanted = "joined" if phase == "joined" else "unjoined"
    if not all(m["state"] == wanted for m in local):
        raise RuntimeError(f"{phase}: local controllers not both {wanted}: " +
                           ", ".join(m["name"] + "=" + m["state"] for m in local))
    if phase == "joined" and len({str(m["group_uid"]).lower() for m in local}) != 1:
        raise RuntimeError("Joined controllers do not report the same group UID")


def report(first, second, third):
    print("\n=== LOCAL CONTROLLERS ===")
    for a, b, c in zip(first["local"], second["local"], third["local"]):
        print(f"{a['name']}: {a['state']} -> {b['state']} -> {c['state']}")
        print(f"  source: {a['source']} -> {b['source']} -> {c['source']}")
        print(f"  synthesized: {a['synthesized']} -> {b['synthesized']} -> {c['synthesized']}")
        print(f"  original restored: {a['fingerprint'] == c['fingerprint']}")
        print(f"  expected saved standalone at end: {c['matches_saved_standalone']}")
    print("\n=== CLOUD OBJECTS ===")
    for label, state in (("before", first), ("joined", second), ("unjoined", third)):
        print(f"{label}: {state['cloud_count']} objects returned")
    before = {str(o["id"]): o for o in first["cloud"]}
    middle = {str(o["id"]): o for o in second["cloud"]}
    after = {str(o["id"]): o for o in third["cloud"]}
    for obj_id in sorted(set(before) | set(middle) | set(after)):
        values = [d.get(obj_id) for d in (before, middle, after)]
        if values[0] != values[1] or values[1] != values[2]:
            names = [v["name"] if v else "<absent>" for v in values]
            print(f"  changed object {obj_id}: " + " -> ".join(names))
            print("    LEDs: " + " -> ".join(str(v["leds"]) if v else "-" for v in values))
            print("    source: " + " -> ".join(str(v["source"]) if v else "-" for v in values))
    print("Cloud object visibility can differ by account; inspect private backups if an object is absent.")


def capture_until_valid(plan, token, timeout, root, prefix, phase, max_attempts=6):
    """Keep each partial state; never assume app join/ungroup is atomic."""
    for attempt in range(1, max_attempts + 1):
        folder = root / f"{prefix}-attempt-{attempt:02d}"
        if folder.exists():
            # Preserve earlier evidence when resuming a partly completed run.
            print(f"Previous attempt retained: {folder}")
            try:
                old = json.loads((folder / "summary-PRIVATE.json").read_text(encoding="utf-8"))
                validate_phase(old, phase)
                return old
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                # An incomplete previous observation is not a valid finished phase.
                # Keep the archived attempt and try the next numbered capture.
                continue
        snapshot = capture(plan, token, timeout, folder)
        try:
            validate_phase(snapshot, phase)
            return snapshot
        except RuntimeError as exc:
            print(f"INCOMPLETE: {exc}")
            print(f"Snapshot retained at {folder}")
            if attempt == max_attempts:
                raise RuntimeError("Maximum observations reached; stop and inspect device state")
            print("Wait for the app operation to finish. If it failed, resolve it in the app.")
            answer = input("Press Enter to recheck, or type q to stop safely: ").strip().lower()
            if answer in ("q", "quit", "stop"):
                raise RuntimeError("User stopped; all earlier snapshots preserved")
    raise RuntimeError("Expected state was not observed")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", default="private-area-plan.json")
    p.add_argument("--output", default="cloud-audits/office-join-cycle")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--resume-run", type=Path, help="Resume existing run folder containing 01-unjoined-before/summary-PRIVATE.json")
    args = p.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout must be within 0..30 seconds")
        plan, _ = load_plan(args.plan)
        if len(plan["members"]) != 2 or plan["size"] != 420:
            raise ValueError("Office test requires exactly 2 controllers and 420 total LEDs")
        token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token()
        if not token:
            raise ValueError("Cloud token absent or expired; run cloud_audit.py --plan private-area-plan.json --login")
        if args.resume_run:
            root = args.resume_run
            baseline = root / "01-unjoined-before" / "summary-PRIVATE.json"
            if not baseline.is_file():
                raise ValueError("Resume folder has no saved unjoined baseline")
            first = json.loads(baseline.read_text(encoding="utf-8"))
            validate_phase(first, "unjoined")
            print("Reusing preserved UNJOINED baseline:", baseline)
            print("Joined step may currently be partial; do not unjoin yet.")
        else:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            root = Path(args.output) / stamp
            print("Reading UNJOINED baseline (cloud GETs, LAN GETs + login/verify only).")
            first = capture(plan, token, args.timeout, root / "01-unjoined-before")
            validate_phase(first, "unjoined")
            print("Baseline saved. JOIN the Office windows in the Twinkly app.")
            input("Press Enter once the app reports the JOIN complete: ")
        second = capture_until_valid(plan, token, args.timeout, root, "02-joined", "joined")
        print("Joined state verified. UNJOIN the Office windows in the Twinkly app.")
        input("Press Enter once the app reports the UNJOIN complete: ")
        third = capture_until_valid(plan, token, args.timeout, root, "03-unjoined-after", "unjoined")
        report(first, second, third)
        print("\nSaved PRIVATE snapshots:", root)
        print("No cloud or LAN grouping writes were initiated by the script.")
        print("DO NOT upload private raw object backups to public GitHub.")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            json.JSONDecodeError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
