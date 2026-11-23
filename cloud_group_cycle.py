#!/usr/bin/env python3
"""Experimental Twinkly CLOUD + LAN group round trip, with human checkpoints.

Check -> Ungroup (LAN+cloud) -> Check -> Group (LAN+cloud) -> Check.
DRY RUN BY DEFAULT. At each transition the operator must type an explicit
word. Never retries cloud creates or attempts automatic rollback.
"""
import argparse
import base64
import copy
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import sys
import uuid
import urllib.error
import urllib.request

from cloud_audit import BASE, NoRedirect, cloud_get
from cloud_group_live import collect, get_cloud_token, read_local
from cloud_group_preflight import FILES, objects_from, coordinates, compare_points, mac, normalized_uid
from group_test import load_plan, layout_matches
from twinkly_inspector import request, authenticate, save_json

GROUP_API = "/xled/v1/application/group"
LAYOUT_API = "/xled/v1/led/layout/full"
CLOUD_SELECTOR = "/v3/objects?mine=true&fields=layout,devices,capabilities"
DEVICE_KEYS = ("name", "fps", "mac", "ledProfile", "family", "uniqueId",
               "networkMode", "productCode", "hwVersion", "password", "ip",
               "capacity", "meta", "ledCount", "firmware", "prodTs")
MAX_BODY = 16 * 1024 * 1024


def cloud_write(method, path, payload, token, expected, timeout):
    """Fixed destination and limited cloud routes, no redirects and no retry."""
    if method == "POST" and path == "/v3/objects":
        pass
    elif method == "PUT" and path.startswith("/v3/installations/") and path.endswith("/objects"):
        middle = path[len("/v3/installations/"):-len("/objects")]
        if not middle.isdecimal() or not middle:
            raise ValueError("Invalid installation ID")
        if payload.get("action") != "remove" or len(payload.get("objectIds", [])) != 1:
            raise ValueError("Only single-object installation removal is supported")
    else:
        raise ValueError("Cloud endpoint/method not allowlisted")
    data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
    if len(data) > MAX_BODY:
        raise ValueError("Cloud request too large")
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "twinkly-api-tools-group-cycle/0.1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as resp:
            if resp.status != expected:
                raise RuntimeError("Unexpected cloud HTTP response; do not retry")
            body = resp.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise RuntimeError("Oversized cloud response; outcome ambiguous")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Cloud write returned HTTP " + str(exc.code) +
                           "; inspect state before retry") from None


def verify_cloud_object_unchanged(original, token, timeout):
    fresh = objects_from(cloud_get(CLOUD_SELECTOR, token, timeout))
    matches = [x for x in fresh if x.get("id") == original.get("id")]
    if len(matches) != 1:
        raise RuntimeError("Cloud target changed/disappeared; refusing write")
    obj = matches[0]
    if obj.get("uuid") != original.get("uuid") or obj.get("ledCount") != original.get("ledCount"):
        raise RuntimeError("Cloud Area identity changed")
    if [mac(s["device"].get("mac")) for s in obj["devices"]] != [
            mac(s["device"].get("mac")) for s in original["devices"]]:
        raise RuntimeError("Cloud Area membership changed")
    if not compare_points(coordinates(obj["layout"]),
                          coordinates(original["layout"]))["matching"]:
        raise RuntimeError("Cloud Area coordinates changed")
    return obj


def find_area(snapshot, plan):
    cloud = objects_from(snapshot[FILES[0]])
    expected = [mac(snapshot[FILES[i + 1]]["gestalt"].get("mac")) for i in range(2)]
    matches = [
        obj for obj in cloud if isinstance(obj.get("devices"), list)
        and [mac(x.get("device", {}).get("mac")) for x in obj["devices"]] == expected]
    if len(matches) != 1 or matches[0].get("ledCount") != plan["size"]:
        raise ValueError("Current cloud Area not uniquely matched by both MACs")
    local_uid = normalized_uid(snapshot[FILES[1]]["group"]["group"].get("uid"))
    cloud_uid = normalized_uid(matches[0].get("uuid"))
    if local_uid is None or local_uid != cloud_uid:
        raise ValueError("Cloud Area UUID differs from the current local group UUID")
    return matches[0]


def find_standalone_templates(plan, identity, roots):
    """Find full 3D object metadata in prior PRIVATE local backups; never use stale identities."""
    found = {m["name"]: [] for m in plan["members"]}
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        paths = list(base.glob("*/objects-private.json")) + list(
            base.glob("*/cloud-objects-PRIVATE.json"))
        for path in paths:
            try:
                if path.stat().st_size > MAX_BODY:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                records = objects_from(data)
            except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError):
                continue
            for o in records:
                if not isinstance(o, dict) or len(o.get("devices", [])) != 1:
                    continue
                device = o["devices"][0].get("device")
                layout = o.get("layout")
                if not isinstance(device, dict) or not isinstance(layout, dict):
                    continue
                for i, member in enumerate(plan["members"]):
                    if (mac(device.get("mac")) != identity[i] or
                            layout.get("source") != "3d" or
                            o.get("ledCount") != member["leds"]):
                        continue
                    try:
                        if compare_points(coordinates(layout),
                                          member["standalone_layout"]["coordinates"])["matching"]:
                            fields = ("geometry", "aspectXY", "aspectXZ", "name", "synthesized")
                            if all(k in layout for k in fields):
                                found[member["name"]].append((path.stat().st_mtime, o))
                    except (ValueError, TypeError, OSError):
                        continue
    result = {}
    for member in plan["members"]:
        options = sorted(found[member["name"]], key=lambda x: x[0], reverse=True)
        if not options:
            raise ValueError("No verified private standalone 3D cloud template for " +
                             member["name"] + ". Stop; original 3D metadata is required.")
        result[member["name"]] = copy.deepcopy(options[0][1])
    return result


def proof_for(ip, timeout):
    """Fresh Twinkly LAN challenge proof needed by cloud create schema."""
    challenge = base64.b64encode(os.urandom(32)).decode("ascii")
    login = request(ip, "/xled/v1/login", method="POST",
                    payload={"challenge": challenge}, timeout=timeout)
    result = login.get("data") if login.get("ok") else None
    if not isinstance(result, dict) or not all(
            isinstance(result.get(k), str) and result[k]
            for k in ("authentication_token", "challenge-response")):
        raise RuntimeError("Device proof challenge failed")
    token = result["authentication_token"]
    verify = request(ip, "/xled/v1/verify", method="POST", token=token,
                     payload={"challenge-response": result["challenge-response"]},
                     timeout=timeout)
    if not verify.get("ok"):
        raise RuntimeError("Device proof verify failed")
    return challenge, result["challenge-response"]


def device_payload(device, ip, timeout):
    if any(k not in device for k in DEVICE_KEYS):
        raise ValueError("Cloud device descriptor missing captured create fields")
    data = {key: copy.deepcopy(device[key]) for key in DEVICE_KEYS}
    challenge, response = proof_for(ip, timeout)
    data["challenge"] = challenge
    data["challengeResponse"] = response
    return data


def encoded_layout(points):
    return base64.b64encode(gzip.compress(
        json.dumps(points, allow_nan=False, separators=(",", ":")).encode()
    )).decode("ascii")


def create_body(template, members, layout, *, name, installation_id, metadata,
                object_uuid=None):
    """Build the captured POST /v3/objects shape, without copying server-owned IDs."""
    if any(k not in template for k in ("capacity", "fps", "ledProfile")):
        raise ValueError("Cloud template missing capacity, fps or ledProfile")
    required = ("source", "coords", "geometry", "uuid", "aspectXZ",
                "aspectXY", "name", "synthesized")
    if any(k not in layout for k in required):
        raise ValueError("Cloud layout template incomplete")
    return {
        "name": name, "installationId": installation_id,
        "ledProfile": template["ledProfile"], "capacity": template["capacity"],
        "devices": members, "fps": template["fps"], "meta": metadata,
        "layout": {k: copy.deepcopy(layout[k]) for k in required},
        "uuid": str(uuid.UUID(str(object_uuid))) if object_uuid else str(uuid.uuid4()),
        "ledCount": sum(
            int(item["device"]["ledCount"]) for item in members),
    }


def local_write(member, path, payload, timeout):
    token, _ = authenticate(member["ip"], timeout)
    if not token:
        raise RuntimeError(member["name"] + ": local authentication unavailable")
    answer = request(member["ip"], path, method="POST", token=token,
                     payload=payload, timeout=timeout)
    if not answer.get("ok") or not isinstance(answer.get("data"), dict) or (
            answer["data"].get("code") != 1000):
        raise RuntimeError(member["name"] + ": local POST ambiguous or failed on " +
                           path + "; no automatic retry")


def check_roles(plan, roles, timeout):
    for i, member in enumerate(plan["members"]):
        data = read_local(member, timeout)
        role = data["group"]["group"]
        if role.get("mode") != roles[i]:
            raise RuntimeError(member["name"] + ": unexpected role after local write")


def checkpoint(title, plan_path, expected, output, timeout):
    report, plan, snapshot = collect(plan_path, timeout=timeout, output=output,
                                     include_snapshot=True)
    print(title, report["result"], report["state"])
    for issue in report["issues"]:
        print("  BLOCK:", issue)
    if report["result"] != "PASS_READ_ONLY" or report["state"] != expected:
        raise RuntimeError(title + " failed; no more changes will be attempted")
    return plan, snapshot


def approve(prompt, phrase):
    print("\n" + prompt)
    if input("Type " + phrase + " to continue (anything else stops): ").strip() != phrase:
        raise RuntimeError("Stopped by operator. No automatic rollback.")


def run(args):
    plan, _ = load_plan(args.plan)
    if len(plan["members"]) != 2 or not 0 < args.timeout <= 30:
        raise ValueError("Only two controllers and timeout 0-30 seconds supported")
    if not args.execute:
        print("READ-ONLY CHECK: verifying the currently grouped cloud and LAN,")
        print("and looking for archived standalone 3D cloud mapping templates.")
        current_plan, current = checkpoint(
            "1 CHECK GROUPED", args.plan, "grouped-cloud", args.output, args.timeout)
        original = find_area(current, current_plan)
        identities = [mac(current[FILES[i + 1]]["gestalt"]["mac"]) for i in range(2)]
        templates = find_standalone_templates(
            current_plan, identities, ("cloud-audits", "cloud-preflight-backups"))
        print("READY FOR CONTROLLED TEST: verified", len(templates),
              "standalone 3D templates; Area object", original["id"])
        print("Dry run: no cloud/LAN writes. Use --execute with confirmations.")
        return 0
    if not args.acknowledge_risk or args.confirm_group != args.group:
        raise ValueError("Execution requires --acknowledge-risk and matching --confirm-group")
    if args.group.casefold() != plan["group_name"].casefold():
        print("Using cloud label", repr(args.group), "(hardware matched by MAC)")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = Path(args.output) / ("cloud-cycle-" + stamp)
    dest.mkdir(parents=True, exist_ok=False)
    log = {"stages": [], "complete": False, "name": args.group}
    try:
        plan, start = checkpoint("1 CHECK GROUPED", args.plan, "grouped-cloud", dest, args.timeout)
        original = find_area(start, plan)
        if args.group.casefold() not in (plan["group_name"].casefold(),
                                         str(original["name"]).casefold()):
            raise ValueError("Requested name does not identify the verified cloud Area")
        if not isinstance(original.get("installation"), dict):
            raise ValueError("Cloud installation reference absent")
        installation = original["installation"]["id"]
        identity = [mac(start[FILES[i + 1]]["gestalt"]["mac"]) for i in range(2)]
        templates = find_standalone_templates(
            plan, identity, ("cloud-audits", "cloud-preflight-backups"))
        save_json(dest / "original-area-private.json", original)
        for m in plan["members"]:
            save_json(dest / ("standalone-template-" + str(m["offset"]) + "-private.json"),
                      templates[m["name"]])
        print("Verified current Area and two archived standalone 3D templates.")
        approve("Stage 2 will UNGROUP both controllers and change the cloud installation. "
                "The app may temporarily show missing objects.", "UNGROUP")
        # Local ungroup: captured app order, group:none then standalone map.
        for member in plan["members"]:
            local_write(member, GROUP_API,
                        {"group": {"mode": "none", "size": 0, "offset": 0}},
                        args.timeout)
            local_write(member, LAYOUT_API, member["standalone_layout"], args.timeout)
            current = read_local(member, args.timeout)
            if current["group"]["group"].get("mode") != "none" or not layout_matches(
                    current["layout"], member["standalone_layout"]):
                raise RuntimeError(member["name"] + ": standalone LAN readback failed")
        log["stages"].append("LAN ungrouped")
        save_json(dest / "cycle-status-private.json", log)
        # Build and validate BOTH object bodies while the original Area is
        # still associated. If either payload is invalid, no cloud write occurs.
        drafts = []
        for i, member in enumerate(plan["members"]):
            template = templates[member["name"]]
            device = original["devices"][i]["device"]
            if mac(device.get("mac")) != identity[i]:
                raise RuntimeError("Member device changed; refusing cloud create")
            layout = {k: template["layout"][k] for k in
                      ("source", "coords", "geometry", "uuid", "aspectXZ",
                       "aspectXY", "name", "synthesized")}
            layout["uuid"] = str(uuid.uuid4())
            layout["coords"] = encoded_layout(member["standalone_layout"]["coordinates"])
            body = create_body(template, [{
                "part": 0, "device": device_payload(device, member["ip"], args.timeout)
            }], layout, name=template["name"],
                               installation_id=installation, metadata=template["meta"])
            if body["ledCount"] != member["leds"] or body["layout"]["source"] != "3d":
                raise RuntimeError("Stand-alone payload shape invalid; no cloud write")
            drafts.append((member["name"], body))
        token = get_cloud_token(timeout=args.timeout)
        verify_cloud_object_unchanged(original, token, args.timeout)
        cloud_write("PUT", "/v3/installations/" + str(installation) + "/objects",
                    {"action": "remove", "objectIds": [original["id"]]},
                    token, 200, args.timeout)
        log["stages"].append("Cloud Area detached")
        save_json(dest / "cycle-status-private.json", log)
        for member_name, body in drafts:
            reply = cloud_write("POST", "/v3/objects", body, token, 201, args.timeout)
            log["stages"].append("Cloud standalone created for " + member_name)
            save_json(dest / "cycle-status-private.json", log)
            print("Cloud standalone created:", member_name,
                  "(response ID:", reply.get("id", "unavailable"), ")")
        plan, standalone = checkpoint("3 CHECK UNGROUPED", args.plan, "ungrouped-cloud",
                                       dest, args.timeout)
        for i, m in enumerate(plan["members"]):
            if not layout_matches(standalone[FILES[i + 1]]["layout"], m["standalone_layout"]):
                raise RuntimeError("Original standalone 3D mapping not restored: " + m["name"])
        approve("Inspect the Twinkly app: there should now be TWO independent Office "
                "controllers with their original 3D layouts. If correct, allow regroup.", "GROUP")
        # Local regroup: 2d layout first, then master/slave roles.
        group_uid = str(uuid.uuid4())
        for i, member in enumerate(plan["members"]):
            local_write(member, LAYOUT_API, member["area_layout"], args.timeout)
            role = "master" if i == 0 else "slave"
            local_write(member, GROUP_API, {"group": {
                "mode": role, "size": plan["size"], "offset": member["offset"],
                "uid": group_uid, "compat_mode": 0,
            }}, args.timeout)
            observed = read_local(member, args.timeout)["group"]["group"]
            if observed.get("mode") != role or observed.get("size") != plan["size"] or (
                    normalized_uid(observed.get("uid")) != normalized_uid(group_uid)):
                raise RuntimeError(member["name"] + ": regroup role readback failed")
        log["stages"].append("LAN regrouped")
        save_json(dest / "cycle-status-private.json", log)
        # Build fresh cloud Area with original 2D geometry, effect metadata, device proofs.
        current = [read_local(m, args.timeout) for m in plan["members"]]
        for i, member in enumerate(plan["members"]):
            if not layout_matches(current[i]["layout"], member["area_layout"]):
                raise RuntimeError(member["name"] + ": Area map changed")
        token = get_cloud_token(timeout=args.timeout)
        layout = {k: original["layout"][k] for k in
                  ("source", "coords", "geometry", "uuid", "aspectXZ",
                   "aspectXY", "name", "synthesized")}
        layout["uuid"] = str(uuid.uuid4())
        layout["coords"] = encoded_layout(
            current[0]["layout"]["coordinates"] + current[1]["layout"]["coordinates"])
        group_members = []
        for i, member in enumerate(plan["members"]):
            group_members.append({
                "part": i,
                "device": device_payload(original["devices"][i]["device"],
                                         member["ip"], args.timeout),
            })
        body = create_body(original, group_members, layout, name=original["name"],
                           installation_id=installation, metadata=original["meta"],
                           object_uuid=group_uid)
        reply = cloud_write("POST", "/v3/objects", body, token, 201, args.timeout)
        log["stages"].append("Cloud Area created")
        save_json(dest / "cycle-status-private.json", log)
        print("New cloud Area object:", reply.get("id", "unavailable"))
        plan, final = checkpoint("5 CHECK REGROUPED", args.plan, "grouped-cloud",
                                  dest, args.timeout)
        for i, member in enumerate(plan["members"]):
            before = start[FILES[i + 1]]["layout"]["coordinates"]
            after = final[FILES[i + 1]]["layout"]["coordinates"]
            if not compare_points(before, after)["matching"]:
                raise RuntimeError("Final Area coordinates differ from starting Area")
        now_cloud = find_area(final, plan)
        if not compare_points(coordinates(original["layout"]),
                              coordinates(now_cloud["layout"]))["matching"]:
            raise RuntimeError("Final cloud Area geometry differs from initial mapping")
        approve("Inspect the Twinkly app: ONE Office group should control both windows. "
                "If playback works, finish.", "DONE")
        log["complete"] = True
        print("ROUND TRIP VERIFIED: cloud and LAN consistency; original Area geometry preserved.")
        return 0
    except (RuntimeError, ValueError, TypeError, KeyError, OSError,
            urllib.error.URLError) as exc:
        log["error"] = str(exc)
        print("STOPPED:", exc, file=sys.stderr)
        print("Do NOT repeat this workflow or retry an ambiguous create.", file=sys.stderr)
        return 2
    finally:
        save_json(dest / "cycle-status-private.json", log)
        print("Private recovery snapshots:", dest)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group", required=True)
    p.add_argument("--plan", default="private-area-plan.json")
    p.add_argument("--output", default="cloud-preflight-backups")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--acknowledge-risk", action="store_true")
    p.add_argument("--confirm-group")
    args = p.parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
