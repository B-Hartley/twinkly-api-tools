#!/usr/bin/env python3
"""Guarded, master-only Twinkly Area repair; DRY RUN by default.

Repairs precisely this state: cloud Area contains both intended MACs and
matching 2D geometry, the left controller is standalone, and the right is
a correctly configured slave. Reuses the right controller's existing group
UUID. Never changes cloud objects, either layout or the right controller.
"""
import argparse
import sys
import uuid

from cloud_group_live import collect
from cloud_group_preflight import FILES, mac, objects_from, coordinates, compare_points
from twinkly_inspector import authenticate, request

GROUP_ENDPOINT = "/xled/v1/application/group"
EXPECTED_ISSUES = frozenset({
    "Local roles are not master/slave",
    "Local group UUIDs missing or different",
    "Local total group size differs from cloud",
})


def repair_candidate(report, plan, snapshot):
    """Validate exact partial state. Return inert payload and identity evidence."""
    if len(plan["members"]) != 2 or plan["members"][0]["offset"] != 0:
        raise ValueError("Only a two-controller Area with left master offset 0 is supported")
    if report.get("state") != "grouped-cloud" or report.get("result") != "BLOCK":
        raise ValueError("The cloud Area is not in the expected partial-join state")
    if set(report.get("issues", [])) != EXPECTED_ISSUES:
        raise ValueError("Unexpected preflight issues: not eligible for master-only repair")
    if not isinstance(report.get("mapping"), dict) or not report["mapping"].get("matching"):
        raise ValueError("All cloud/local coordinates must match before repair")
    if len(report.get("matched_objects", [])) != 1:
        raise ValueError("Exactly one cloud group must match")
    left = snapshot[FILES[1]]
    right = snapshot[FILES[2]]
    lrole, rrole = left["group"]["group"], right["group"]["group"]
    if lrole.get("mode") != "none" or rrole.get("mode") != "slave":
        raise ValueError("Only missing-master / existing-slave state can be repaired")
    if rrole.get("size") != plan["size"] or rrole.get("offset") != plan["members"][1]["offset"]:
        raise ValueError("Slave size or offset differs from saved plan")
    if left["gestalt"].get("number_of_led") != plan["members"][0]["leds"]:
        raise ValueError("Master LED count differs from saved plan")
    if right["gestalt"].get("number_of_led") != plan["members"][1]["leds"]:
        raise ValueError("Slave LED count differs from saved plan")
    if left["layout"].get("source") != "2d" or right["layout"].get("source") != "2d":
        raise ValueError("Both local layouts must already be 2D")
    target_uid = str(uuid.UUID(str(rrole.get("uid"))))
    if len(objects_from(snapshot[FILES[0]])) == 0:
        raise ValueError("Cloud objects missing")
    matching = [
        o for o in objects_from(snapshot[FILES[0]])
        if o.get("id") == report["matched_objects"][0]["id"]
    ]
    if len(matching) != 1:
        raise ValueError("Cloud group object ID not unique")
    cloud = matching[0]
    if cloud.get("ledCount") != plan["size"]:
        raise ValueError("Cloud LED count differs from saved plan")
    cloud_macs = [mac(s["device"].get("mac")) for s in cloud["devices"]]
    local_macs = [mac(left["gestalt"].get("mac")), mac(right["gestalt"].get("mac"))]
    if cloud_macs != local_macs or None in local_macs:
        raise ValueError("Cloud member identity/order differs from both local devices")
    if [s.get("part") for s in cloud["devices"]] != [0, 1]:
        raise ValueError("Unexpected cloud member part order")
    payload = {"group": {"mode": "master", "size": plan["size"],
                         "offset": 0, "uid": target_uid, "compat_mode": 0}}
    # Compare only safety-critical state. Full device/gestalt API replies contain
    # changing telemetry and cloud metadata; byte-for-byte JSON comparison
    # caused false drift alarms even when the group state was unchanged.
    evidence = {
        "cloud_id": cloud["id"],
        "cloud_members": [(slot["part"], mac(slot["device"].get("mac")))
                          for slot in cloud["devices"]],
        "cloud_led_count": cloud["ledCount"],
        "cloud_source": cloud["layout"]["source"],
        "cloud_coords": coordinates(cloud["layout"]),
        "local": [
            {
                "mac": mac(data["gestalt"].get("mac")),
                "led_count": data["gestalt"].get("number_of_led"),
                "group": {key: data["group"]["group"].get(key)
                          for key in ("mode", "uid", "size", "offset", "compat_mode")},
                "source": data["layout"].get("source"),
                "coords": data["layout"]["coordinates"],
            }
            for data in (left, right)
        ],
    }
    return payload, evidence


def critical_drift(baseline, current):
    """Return changes in safety-critical identity, roles, or mapping only."""
    problems = []
    for key in ("cloud_id", "cloud_members", "cloud_led_count", "cloud_source"):
        if baseline[key] != current[key]:
            problems.append("cloud " + key + " changed")
    if not compare_points(baseline["cloud_coords"], current["cloud_coords"])["matching"]:
        problems.append("cloud LED coordinates changed")
    for index, label in enumerate(("Left", "Right")):
        before, after = baseline["local"][index], current["local"][index]
        for key in ("mac", "led_count", "group", "source"):
            if before[key] != after[key]:
                problems.append(label + " " + key + " changed")
        if not compare_points(before["coords"], after["coords"])["matching"]:
            problems.append(label + " LED coordinates changed")
    return problems


def validate_group_label(label, report, plan):
    names = {plan["group_name"].casefold()}
    names.update(str(o["name"]).casefold() for o in report["matched_objects"])
    if label.casefold() not in names:
        raise ValueError("Group label does not match saved plan or hardware-verified cloud object")


def run(group, plan_path, *, execute=False, acknowledged=False,
        group_confirm=None, master_confirm=None, timeout=10,
        backup_root="cloud-preflight-backups"):
    if execute and (not acknowledged or group_confirm != group or not master_confirm):
        raise ValueError("Execution requires --acknowledge-risk, exact --group-confirm, and --master-confirm")
    report, plan, snap = collect(plan_path, timeout=timeout, output=backup_root,
                                 include_snapshot=True)
    validate_group_label(group, report, plan)
    if execute and master_confirm != plan["members"][0]["name"]:
        raise ValueError("Master confirmation does not match the first controller in the private plan")
    payload, fingerprint = repair_candidate(report, plan, snap)
    print("Candidate: cloud group", report["matched_objects"][0]["id"],
          "| local Left=none, Right=slave | mapping=matching")
    print("Only proposed write: POST", GROUP_ENDPOINT, "to", plan["members"][0]["name"])
    print("Master will use the *current slave UUID*; Right, cloud and layouts are unchanged.")
    if not execute:
        print("DRY RUN: no grouping changes made. Pass explicit confirmation flags to execute.")
        return 0

    # Re-read both LAN controllers and cloud immediately before the single write.
    second, second_plan, second_snap = collect(plan_path, timeout=timeout,
                                               output=backup_root, include_snapshot=True)
    validate_group_label(group, second, second_plan)
    latest_payload, latest_fingerprint = repair_candidate(second, second_plan, second_snap)
    drift = critical_drift(fingerprint, latest_fingerprint)
    if payload != latest_payload or drift:
        raise RuntimeError("Safety-critical cloud/LAN state changed: " +
                           ", ".join(drift or ["proposed payload changed"]) +
                           "; no write attempted")
    master_ip = plan["members"][0]["ip"]
    token, _ = authenticate(master_ip, timeout)
    if not token:
        raise RuntimeError("Master LAN authentication failed; no write attempted")
    # Fresh post-auth read of group role, just before modifying it.
    current = request(master_ip, GROUP_ENDPOINT, token=token, timeout=timeout)
    current_role = (current.get("data") or {}).get("group") if current.get("ok") else None
    if not isinstance(current_role, dict) or current_role.get("mode") != "none":
        raise RuntimeError("Master group state changed; no write attempted")
    print("Sending exactly one controller group-role POST; no automatic retry.")
    response = request(master_ip, GROUP_ENDPOINT, method="POST", token=token,
                       payload=payload, timeout=timeout)
    if not response.get("ok") or not isinstance(response.get("data"), dict) or response["data"].get("code") != 1000:
        print("WARNING: write failed or outcome ambiguous; do NOT retry automatically.")
        return 2
    print("Controller accepted write. Performing fresh independent cloud/LAN readback.")
    after, _, _ = collect(plan_path, timeout=timeout, output=backup_root,
                          include_snapshot=True)
    if after["result"] != "PASS_READ_ONLY" or after["state"] != "grouped-cloud":
        print("WARNING: post-write preflight did not pass. No more changes made.")
        for issue in after["issues"]:
            print("  BLOCK:", issue)
        return 2
    print("PASS_READ_ONLY: cloud Area, both local roles and all coordinates agree.")
    print("No cloud writes, no layout uploads, and no writes to Office Right were performed.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group", required=True)
    p.add_argument("--plan", default="private-area-plan.json")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--backup-root", default="cloud-preflight-backups")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--acknowledge-risk", action="store_true")
    p.add_argument("--group-confirm")
    p.add_argument("--master-confirm")
    args = p.parse_args(argv)
    try:
        return run(args.group, args.plan, execute=args.execute,
                   acknowledged=args.acknowledge_risk,
                   group_confirm=args.group_confirm, master_confirm=args.master_confirm,
                   timeout=args.timeout, backup_root=args.backup_root)
    except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
        print("BLOCK:", str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
