#!/usr/bin/env python3
"""Read-only Twinkly cloud inventory audit (experimental, unofficial).

Never writes to the cloud or LAN. Reads a bearer token from an environment
variable or performs an interactive login; never sends credentials outside https://api.twinkly.com.
Cloud response backups contain sensitive data and stay on the user's PC.
"""
import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from cloud_auth import interactive_login
from cloud_token_store import renewable_access_token as cached_access_token, store_session, clear_session
from group_test import load_plan, layout_matches
from twinkly_inspector import save_json

BASE = "https://api.twinkly.com"
MAX_BODY = 16 * 1024 * 1024
MAX_DECOMPRESSED = 8 * 1024 * 1024


def cloud_get(path, token, timeout=10):
    """Fixed-path HTTPS GET only, no caller-controlled domains or redirects."""
    if path not in ("/v3/objects?mine=true&fields=layout,devices,capabilities", "/v3/installations"):
        raise ValueError("Only allowlisted cloud GET paths supported")
    request = urllib.request.Request(BASE + path, headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
        "User-Agent": "twinkly-api-tools-cloud-audit/0.1",
    }, method="GET")
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError("Cloud response too large")
            content = json.loads(body)
            if not isinstance(content, (dict, list)):
                raise ValueError("Cloud returned unexpected JSON root")
            return content
    except urllib.error.HTTPError as e:
        # Do not echo body, which may contain private account details.
        raise RuntimeError(f"Cloud GET {path} returned HTTP {e.code}") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Cloud redirects not permitted (credential protection)")


def objects_from(data):
    """Unwrap known list-like shapes; refuse unknown shapes rather than guessing."""
    if isinstance(data, list):
        return data
    for field in ("objects", "items", "data", "results"):
        value = data.get(field)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in ("objects", "items", "results"):
                if isinstance(value.get(nested), list):
                    return value[nested]
    raise ValueError("Unrecognised /v3/objects response format; backup saved for local review")


def decode_coords(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_BODY:
        raise ValueError("Invalid encoded coordinates")
    compressed = base64.b64decode(encoded, validate=True)
    with gzip.GzipFile(fileobj=__import__("io").BytesIO(compressed)) as stream:
        blob = stream.read(MAX_DECOMPRESSED + 1)
    if len(blob) > MAX_DECOMPRESSED:
        raise ValueError("Decompressed coordinates too large")
    points = json.loads(blob)
    if not isinstance(points, list) or len(points) > 100000:
        raise ValueError("Coordinates must be bounded JSON list")
    if any(not isinstance(p, dict) or not all(axis in p for axis in ("x", "y", "z"))
           for p in points):
        raise ValueError("Unexpected point format")
    return points


def summarise(obj, plan):
    if not isinstance(obj, dict):
        raise ValueError("Unexpected non-object in cloud list")
    devices = obj.get("devices")
    devices = devices if isinstance(devices, list) else []
    descriptions = []
    for slot in devices:
        if not isinstance(slot, dict):
            continue
        d = slot.get("device")
        if not isinstance(d, dict):
            d = slot
        descriptions.append({
            "name": str(d.get("name", "")),
            "product_code": str(d.get("productCode", "")),
            "member_part": slot.get("part"),
        })
    layout = obj.get("layout")
    info = {"id": obj.get("id"), "name": str(obj.get("name", "")),
            "led_count": obj.get("ledCount"), "member_count": len(devices),
            "members": descriptions, "layout_source": None,
            "coordinate_count": None, "coordinate_state": "unavailable",
            "installation_id": (obj.get("installationId") or
                                (obj.get("installation") or {}).get("id")
                                if isinstance(obj.get("installation"), dict) else obj.get("installationId"))}
    if isinstance(layout, dict):
        info["layout_source"] = layout.get("source")
        if layout.get("coords") is not None:
            try:
                coords = decode_coords(layout["coords"])
                info["coordinate_count"] = len(coords)
                # Compare only a single-controller object with a single member; never
                # equate the combined 420 LEDs to a 210 LED standalone mapping.
                if len(devices) == 1:
                    choices = []
                    for member in plan["members"]:
                        match_standalone = layout_matches(
                            {"source": layout.get("source"), "synthesized": layout.get("synthesized"),
                             "coordinates": coords}, member["standalone_layout"])
                        match_area = layout_matches(
                            {"source": layout.get("source"), "synthesized": layout.get("synthesized"),
                             "coordinates": coords}, member["area_layout"])
                        if match_standalone:
                            choices.append(member["name"] + ":saved-standalone")
                        if match_area:
                            choices.append(member["name"] + ":saved-area")
                    info["coordinate_state"] = ", ".join(choices) or "different-from-saved-mappings"
                else:
                    info["coordinate_state"] = "combined-layout-not-automatically-compared"
            except (ValueError, OSError, EOFError, TypeError, json.JSONDecodeError) as e:
                info["coordinate_state"] = "unreadable-encoded-geometry:" + type(e).__name__
    return info



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="Private saved local group plan")
    parser.add_argument("--output", default="cloud-audits")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--login", action="store_true", help="Prompt for account email/password and login in memory")
    parser.add_argument("--username", help="Email to use with --login; password is always prompted")
    parser.add_argument("--forget-login", action="store_true", help="Delete locally encrypted cloud session")
    args = parser.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("Timeout out of range")
        if args.username and not args.login:
            raise ValueError("--username requires --login")
        if args.forget_login:
            if args.login or args.username:
                raise ValueError("--forget-login cannot be combined with --login/--username")
            clear_session()
            print("Local encrypted Twinkly cloud token cache deleted.")
            return 0
        if args.login:
            session = interactive_login(args.username, args.timeout, return_session=True)
            token = session["access_token"]
            if session["expires_in"] > 0:
                store_session(session)
                print("Encrypted cloud session saved to Windows user profile.")
            else:
                print("Token lifetime unknown: not caching session.")
        else:
            token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token(timeout=args.timeout)
            if token:
                print("Using existing Twinkly cloud access token.")
            else:
                raise ValueError("Cached session missing or cannot renew; use --login.")
        if not token or any(c.isspace() for c in token) or len(token) > 4096:
            raise ValueError("Use --login or a valid TWINKLY_CLOUD_TOKEN")
        plan, _ = load_plan(args.plan)
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = Path(args.output) / now
        if dest.exists():
            raise ValueError("Audit folder already exists")
        print("Reading cloud objects and installations over HTTPS (GET only).")
        objects = cloud_get("/v3/objects?mine=true&fields=layout,devices,capabilities", token, args.timeout)
        installations = cloud_get("/v3/installations", token, args.timeout)
        dest.mkdir(parents=True)
        # Preserve full original content privately before attempting to parse anything.
        save_json(dest / "objects-private.json", objects)
        save_json(dest / "installations-private.json", installations)
        entries = objects_from(objects)
        if isinstance(objects, dict) and objects.get("next_page") is not None:
            raise ValueError("Cloud results are paginated; refusing incomplete inventory. Private backup saved.")
        if isinstance(objects, dict) and isinstance(objects.get("count"), int) and objects["count"] != len(entries):
            raise ValueError("Cloud object count disagrees with returned records; private backup saved.")
        result = [summarise(obj, plan) for obj in entries]
        save_json(dest / "summary-private.json", {"objects": result, "total_objects": len(entries)})
        print(f"Cloud API returned {len(entries)} object records. Private backup: {dest}")
        print("Displaying EVERY returned object (not just Office matches).")
        print("Note: mine=true may not include all objects shared through an installation.")
        for item in sorted(result, key=lambda x: (str(x["installation_id"] or ""), str(x["name"]))):
            # LED count alone is not identity: numerous unrelated controllers
            # have the same LED count. Require name or exact member set match.
            expected_names = {m["name"].casefold() for m in plan["members"]}
            object_member_names = {str(m["name"]).casefold() for m in item["members"]}
            office_match = (item["name"].casefold() == plan["group_name"].casefold() or
                            item["name"].casefold() in expected_names or
                            (len(object_member_names) == len(expected_names) and
                             object_member_names == expected_names))
            print(f"{'[OFFICE CANDIDATE] ' if office_match else ''}"
                  f"Installation={item['installation_id'] or '?'} "
                  f"Object={item['id']} name={item['name']!r} "
                  f"LEDs={item['led_count']} members={item['member_count']} "
                  f"layout={item['layout_source']} mapping={item['coordinate_state']}")
        print("READ ONLY: no cloud objects or controller mappings were modified.")
        print("Raw backups include private account/device information; do NOT commit or upload publicly.")
        return 0
    except (ValueError, RuntimeError, urllib.error.URLError, OSError, TypeError,
            KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
