#!/usr/bin/env python3
"""Reverse Twinkly Area round trip: CHECK standalone -> GROUP -> CHECK -> UNGROUP -> CHECK.

Uses existing, experimentally validated cloud/LAN request helpers. Cloud writes
are irreversible transactions with no automatic retry. Three live checkpoints
and explicit app-review pauses. Never silently replaces a 3D reference map.
"""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import uuid
import urllib.error

import archive_3d_maps as refs
import cloud_group_cycle as ops
from cloud_group_preflight import FILES, objects_from, coordinates, compare_points, mac, normalized_uid
from cloud_group_live import collect, get_cloud_token, read_local
from group_test import load_plan, layout_matches
from twinkly_inspector import save_json


def identities(snapshot, plan):
    result = []
    for index, member in enumerate(plan["members"]):
        local = snapshot[FILES[index + 1]]
        addr, count = refs.identity(local["gestalt"])
        if count != member["leds"]:
            raise ValueError(member["name"] + ": LED count changed")
        result.append(addr)
    if len(set(result)) != 2:
        raise ValueError("Controller MACs are not unique")
    return result


def standalone_objects(snapshot, plan):
    expected = identities(snapshot, plan)
    found = {}
    for obj in objects_from(snapshot[FILES[0]]):
        devices = obj.get("devices")
        if not isinstance(devices, list) or len(devices) != 1:
            continue
        member = devices[0].get("device")
        if not isinstance(member, dict):
            continue
        addr = mac(member.get("mac"))
        if addr not in expected:
            continue
        if addr in found:
            raise ValueError("Duplicate standalone cloud object for an Office controller")
        idx = expected.index(addr)
        desired = plan["members"][idx]
        local = snapshot[FILES[idx + 1]]
        if obj.get("ledCount") != desired["leds"]:
            raise ValueError(desired["name"] + ": cloud LED count differs")
        if not isinstance(obj.get("layout"), dict) or obj["layout"].get("source") != "3d":
            raise ValueError(desired["name"] + ": cloud 3D mapping unavailable")
        cloud_points = coordinates(obj["layout"])
        ref_layout = refs.verify_layout(local["layout"], desired["leds"])
        if not compare_points(cloud_points, ref_layout["coordinates"])["matching"]:
            raise ValueError(desired["name"] + ": cloud/local 3D geometry differs")
        found[addr] = obj
    if set(found) != set(expected):
        raise ValueError("Expected exactly two distinct standalone Office cloud objects")
    return [found[addr] for addr in expected]


def installation_id(objects):
    found = []
    for obj in objects:
        i = obj.get("installation")
        if isinstance(i, dict):
            found.append(i.get("id"))
        else:
            found.append(obj.get("installationId"))
    if not all(isinstance(v, int) and v > 0 for v in found) or len(set(found)) != 1:
        raise ValueError("Standalones do not have one verified installation")
    return found[0]


def archived_group_template(plan, expected_macs, roots):
    """Find a previous 2D Area cloud object with exact membership/geometry."""
    candidates = []
    expected_points = sum((m["area_layout"]["coordinates"] for m in plan["members"]), [])
    for root in roots:
        folder = Path(root)
        if not folder.is_dir():
            continue
        paths = list(folder.rglob("cloud-objects-PRIVATE.json")) + list(
            folder.rglob("original-area-private.json"))
        if len(paths) > 1500:
            raise ValueError("Too many cloud backups: narrow search directories")
        for path in paths:
            try:
                if path.stat().st_size > ops.MAX_BODY:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                objects = [data] if path.name == "original-area-private.json" else objects_from(data)
                for obj in objects:
                    devices = obj.get("devices")
                    layout = obj.get("layout")
                    if not isinstance(devices, list) or not isinstance(layout, dict):
                        continue
                    actual = [mac(slot.get("device", {}).get("mac"))
                              for slot in devices if isinstance(slot, dict)]
                    if (actual != expected_macs or obj.get("ledCount") != plan["size"]
                            or layout.get("source") != "2d"):
                        continue
                    if not compare_points(coordinates(layout), expected_points)["matching"]:
                        continue
                    if not all(k in layout for k in
                               ("coords", "uuid", "geometry", "aspectXZ", "aspectXY", "name", "synthesized")):
                        continue
                    if not all(k in obj for k in ("capacity", "fps", "ledProfile", "meta", "uuid")):
                        continue
                    candidates.append((path.stat().st_mtime, copy.deepcopy(obj)))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
    if not candidates:
        raise ValueError("No verified former 2D cloud Area template found in private backups")
    return max(candidates, key=lambda item: item[0])[1]


def reference_check(snapshot, plan, root, create=False):
    ids = identities(snapshot, plan)
    for i, member in enumerate(plan["members"]):
        local = snapshot[FILES[i + 1]]
        layout = refs.verify_layout(local["layout"], member["leds"])
        saved = refs.verify_layout(member["standalone_layout"], member["leds"])
        if not compare_points(layout["coordinates"], saved["coordinates"])["matching"]:
            raise ValueError(member["name"] + ": current 3D layout differs from saved original plan")
        if create:
            refs.archive(root, ids[i], member["leds"], layout, member["name"],
                         "captured-live-standalone-3d-verified-against-plan")
        reference = refs.read_reference(root, ids[i], member["leds"])
        if not compare_points(reference["layout"]["coordinates"],
                              layout["coordinates"])["matching"]:
            raise ValueError(member["name"] + ": live 3D mapping differs from immutable reference")
    return ids


def cloud_fresh_standalones(previous, token, timeout):
    newest = objects_from(ops.cloud_get(ops.CLOUD_SELECTOR, token, timeout))
    for item in previous:
        matches = [obj for obj in newest if obj.get("id") == item.get("id")]
        if len(matches) != 1:
            raise RuntimeError("Cloud standalone disappeared; refusing group creation")
        obj = matches[0]
        if obj.get("uuid") != item.get("uuid") or obj.get("devices") != item.get("devices"):
            raise RuntimeError("Cloud standalone identity/membership changed")
        if not compare_points(coordinates(obj["layout"]),
                              coordinates(item["layout"]))["matching"]:
            raise RuntimeError("Cloud standalone map changed")


def new_area_body(template, standalone, plan, installation, uid, timeout):
    layout = copy.deepcopy(template["layout"])
    layout["uuid"] = str(uuid.uuid4())
    layout["coords"] = ops.encoded_layout(
        sum((m["area_layout"]["coordinates"] for m in plan["members"]), []))
    devices = []
    for i, member in enumerate(plan["members"]):
        d = standalone[i]["devices"][0]["device"]
        if mac(d.get("mac")) is None:
            raise ValueError("Controller MAC missing in cloud device descriptor")
        devices.append({"part": i, "device": ops.device_payload(
            d, member["ip"], timeout)})
    return ops.create_body(template, devices, layout,
                           name=template["name"], installation_id=installation,
                           metadata=template["meta"], object_uuid=uid)


def standalone_create_bodies(original, plan, installation, timeout):
    bodies = []
    for index, member in enumerate(plan["members"]):
        template = original[index]
        layout = copy.deepcopy(template["layout"])
        layout["uuid"] = str(uuid.uuid4())
        layout["coords"] = ops.encoded_layout(member["standalone_layout"]["coordinates"])
        device = template["devices"][0]["device"]
        member_payload = ops.device_payload(device, member["ip"], timeout)
        body = ops.create_body(template, [{"part": 0, "device": member_payload}],
                               layout, name=template["name"], installation_id=installation,
                               metadata=template["meta"])
        if body["ledCount"] != member["leds"] or body["layout"]["source"] != "3d":
            raise ValueError("Standalone cloud object construction failed")
        bodies.append(body)
    return bodies


def run_reverse(args):
    plan, _ = load_plan(args.plan)
    if len(plan["members"]) != 2 or not 0 < args.timeout <= 30:
        raise ValueError("Only two-controller Area with timeout 0-30 is supported")
    if not args.execute:
        print("READ-ONLY CHECK: verify both standalone Office lights, archive their 3D maps,")
        print("and locate a verified archived 2D cloud Area template.")
        plan, initial = ops.checkpoint("1 CHECK STANDALONE", args.plan, "ungrouped-cloud",
                                       args.output, args.timeout)
        ids = reference_check(initial, plan, "3d-map-archive", create=True)
        standalones = standalone_objects(initial, plan)
        template = archived_group_template(
            plan, ids, ("cloud-preflight-backups", "cloud-audits"))
        installation_id(standalones)
        print("READY FOR REVERSE CYCLE: immutable 3D archives valid, 2 cloud objects,")
        print("2D Area template found, previous Area name:", template.get("name"))
        print("DRY RUN: no cloud or LAN writes. Execute only after reviewing this output.")
        return 0
    if not args.acknowledge_risk or args.confirm_group != args.group:
        raise ValueError("Execution requires --acknowledge-risk and exact --confirm-group")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(args.output) / ("reverse-cycle-" + stamp)
    destination.mkdir(parents=True, exist_ok=False)
    journal = {"workflow": "standalone-group-standalone", "group_name": args.group,
               "stages": [], "complete": False}
    try:
        plan, original = ops.checkpoint("1 CHECK STANDALONE", args.plan,
                                         "ungrouped-cloud", destination, args.timeout)
        ids = reference_check(original, plan, "3d-map-archive", create=True)
        standalones = standalone_objects(original, plan)
        template = archived_group_template(
            plan, ids, ("cloud-preflight-backups", "cloud-audits"))
        installation = installation_id(standalones)
        save_json(destination / "initial-cloud-standalones-private.json", standalones)
        save_json(destination / "former-area-template-private.json", template)
        journal["stages"].append("Original 3D cloud and LAN mappings backed up")
        save_json(destination / "reverse-cycle-status-private.json", journal)
        ops.approve("Stage 2 will GROUP locally and create a NEW cloud Area. "
                    "Confirm that you have reviewed the saved 3D archives.", "GROUP")
        token = get_cloud_token(timeout=args.timeout)
        cloud_fresh_standalones(standalones, token, args.timeout)
        # Construct cloud object BEFORE controller modifications.
        uid = str(uuid.uuid4())
        area_body = new_area_body(template, standalones, plan, installation, uid,
                                  args.timeout)
        for i, member in enumerate(plan["members"]):
            state = read_local(member, args.timeout)
            if state["group"]["group"].get("mode") != "none":
                raise RuntimeError(member["name"] + ": controller no longer standalone")
            reference_check_single = refs.read_reference(
                "3d-map-archive", ids[i], member["leds"])
            if not layout_matches(state["layout"], reference_check_single["layout"]):
                raise RuntimeError(member["name"] + ": original 3D map changed")
        for i, member in enumerate(plan["members"]):
            ops.local_write(member, ops.LAYOUT_API, member["area_layout"], args.timeout)
            role = "master" if i == 0 else "slave"
            ops.local_write(member, ops.GROUP_API, {"group": {
                "mode": role, "size": plan["size"], "offset": member["offset"],
                "uid": uid, "compat_mode": 0,
            }}, args.timeout)
            checked = read_local(member, args.timeout)
            g = checked["group"]["group"]
            if (g.get("mode") != role or g.get("size") != plan["size"]
                    or normalized_uid(g.get("uid")) != normalized_uid(uid)
                    or not layout_matches(checked["layout"], member["area_layout"])):
                raise RuntimeError(member["name"] + ": Area local readback failed")
        journal["stages"].append("Local Area grouped")
        save_json(destination / "reverse-cycle-status-private.json", journal)
        reply = ops.cloud_write("POST", "/v3/objects", area_body, token, 201, args.timeout)
        journal["stages"].append("Cloud Area created; ID " + str(reply.get("id")))
        save_json(destination / "reverse-cycle-status-private.json", journal)
        plan, grouped = ops.checkpoint("3 CHECK GROUPED", args.plan,
                                       "grouped-cloud", destination, args.timeout)
        area = ops.find_area(grouped, plan)
        if not compare_points(coordinates(area["layout"]),
                              coordinates(template["layout"]))["matching"]:
            raise RuntimeError("New group geometry differs from former 2D Area")
        ops.approve("Check the Twinkly app. The Office lights should now work as ONE Area. "
                    "If correct, proceed to ungroup.", "UNGROUP")
        # Build standalone requests before removing cloud Area.
        prepared = standalone_create_bodies(standalones, plan, installation,
                                             args.timeout)
        for i, member in enumerate(plan["members"]):
            current = read_local(member, args.timeout)
            if (current["group"]["group"].get("mode") !=
                    ("master" if i == 0 else "slave")):
                raise RuntimeError("Current grouped role changed; refusing ungroup")
        for member in plan["members"]:
            ops.local_write(member, ops.GROUP_API,
                            {"group": {"mode": "none", "size": 0, "offset": 0}},
                            args.timeout)
            ops.local_write(member, ops.LAYOUT_API, member["standalone_layout"],
                            args.timeout)
            restored = read_local(member, args.timeout)
            if restored["group"]["group"].get("mode") != "none":
                raise RuntimeError(member["name"] + ": local ungroup failed")
        journal["stages"].append("Local Area ungrouped")
        save_json(destination / "reverse-cycle-status-private.json", journal)
        token = get_cloud_token(timeout=args.timeout)
        ops.verify_cloud_object_unchanged(area, token, args.timeout)
        ops.cloud_write("PUT", "/v3/installations/" + str(installation) + "/objects",
                        {"action": "remove", "objectIds": [area["id"]]},
                        token, 200, args.timeout)
        journal["stages"].append("Cloud Area detached")
        save_json(destination / "reverse-cycle-status-private.json", journal)
        for i, body in enumerate(prepared):
            reply = ops.cloud_write("POST", "/v3/objects", body, token, 201, args.timeout)
            journal["stages"].append("Cloud standalone created: " + plan["members"][i]["name"])
            save_json(destination / "reverse-cycle-status-private.json", journal)
            print("Created cloud standalone", plan["members"][i]["name"], reply.get("id"))
        plan, final = ops.checkpoint("5 CHECK STANDALONE", args.plan,
                                     "ungrouped-cloud", destination, args.timeout)
        if identities(final, plan) != ids:
            raise RuntimeError("Controller identities changed during round trip")
        reference_check(final, plan, "3d-map-archive", create=False)
        end = standalone_objects(final, plan)
        for index in range(2):
            if not compare_points(coordinates(standalones[index]["layout"]),
                                  coordinates(end[index]["layout"]))["matching"]:
                raise RuntimeError("Standalone cloud 3D mapping differs from original")
        ops.approve("Check the Twinkly app: there should be TWO independent Office "
                    "controllers, each with its original 3D mapping.", "DONE")
        journal["complete"] = True
        print("REVERSE CYCLE PASS: standalone -> grouped -> standalone; 3D reference preserved.")
        return 0
    except (RuntimeError, ValueError, KeyError, TypeError, OSError,
            urllib.error.URLError) as exc:
        journal["error"] = str(exc)
        print("STOPPED:", exc, file=sys.stderr)
        print("No automatic retries or rollback. Do not rerun after a partial write.",
              file=sys.stderr)
        return 2
    finally:
        save_json(destination / "reverse-cycle-status-private.json", journal)
        print("Private recovery backups:", destination)
