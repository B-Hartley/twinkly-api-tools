"""Read-only Front cloud discovery. Never guesses compiled-effect ownership."""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from cloud_audit import cloud_get, objects_from, cached_access_token, NoRedirect, BASE, MAX_BODY

def mac(value):
    return str(value).lower().replace(":", "").replace("-", "")

def runtime_get(object_id, token, timeout):
    if type(object_id) is not int or object_id <= 0:
        raise ValueError("Invalid cloud object ID")
    path = f"/v3/runtime/objects/{object_id}"
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
    }, method="GET")
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as reply:
        raw = reply.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError("Cloud runtime response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Unknown runtime response shape")
    return value

def inspect_cloud(members, timeout):
    token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token(timeout=timeout)
    if not token:
        raise ValueError("Cloud login required: run cloud_audit.py --login from repository root")
    raw = cloud_get("/v3/objects?mine=true&fields=layout,devices,capabilities", token, timeout)
    objects = objects_from(raw)
    if isinstance(raw, dict) and (raw.get("next_page") is not None or
            (type(raw.get("count")) is int and raw["count"] != len(objects))):
        raise ValueError("Incomplete cloud inventory")
    expected = {mac(m["mac"]) for m in members}
    uid = str(members[0]["group_uid"]).lower()
    matches = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        slots = obj.get("devices") or []
        if len(slots) != len(members):
            continue
        observed = {mac(slot.get("device", {}).get("mac")) for slot in slots}
        if observed == expected and str(obj.get("uuid", "")).lower() == uid and obj.get("ledCount") == 4900:
            matches.append(obj)
    if len(matches) != 1:
        raise ValueError("Expected exactly one cloud object matching all 15 Front controllers and group UUID")
    obj = matches[0]
    runtime = runtime_get(obj["id"], token, timeout)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = ROOT / f"private-movie-cloud-{stamp}.json"
    with path.open("x", encoding="utf-8") as f:
        json.dump({"object": obj, "runtime": runtime}, f, indent=2)
    print("Verified exact Front cloud object. Runtime keys:", ", ".join(sorted(runtime)))
    print("Saved private cloud metadata:", path.name)
    print("Cloud compiled movie ownership is not yet verified; no cloud writes permitted.")
