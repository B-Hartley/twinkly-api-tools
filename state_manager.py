#!/usr/bin/env python3
"""Compare named Twinkly snapshots and generate a non-destructive restore plan."""
import argparse
import json
from pathlib import Path
import sys


def resolve(root, name):
    directory = Path(root) / name
    if not directory.is_dir():
        raise ValueError(f"Named snapshot not found: {directory}")
    snapshots = sorted((p for p in directory.iterdir() if (p / "manifest.json").is_file()), reverse=True)
    if not snapshots:
        raise ValueError(f"No complete snapshot in {directory}")
    return snapshots[0]


def read_state(snapshot):
    root = Path(snapshot)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    devices = {}
    for c in manifest.get("controllers", []):
        item = {"name": c["name"], "ip": c["ip"], "status": c.get("status"),
                "identity": c.get("identity", {}), "layout_sha256": (
                    c.get("endpoints", {}).get("led_layout_full", {}).get("sha256"))}
        folder = root / "controllers" / c["ip"].replace(".", "_") / "responses"
        movie = folder / "led_movie_config.json"
        if movie.is_file():
            response = json.loads(movie.read_text(encoding="utf-8"))
            data = response.get("data") or {}
            sync = data.get("sync") or {}
            if isinstance(sync, dict):
                item["role"] = sync.get("mode", "unknown")
                item["group_id"] = sync.get("master_id") or sync.get("slave_id")
        # Identity should be compared by hardware identifiers when present.
        devices[c["ip"]] = item
    return {"name": manifest.get("state_name") or root.name,
            "path": str(root), "devices": devices}


def group_members(state, group_id=None):
    devices = list(state["devices"].values())
    groups = {}
    for item in devices:
        key = item.get("group_id")
        if key and item.get("role") in {"master", "slave"}:
            groups.setdefault(key, []).append(item)
    if group_id:
        matches = {key: members for key, members in groups.items()
                   if key.casefold().startswith(group_id.casefold())}
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one matching group for '{group_id}', got {len(matches)}")
        return matches
    return groups


def analyse(before, after, group_id=None):
    groups = group_members(before, group_id)
    # Refuse ambiguous snapshot selection. The user's Front group must be explicit
    # if other recorded groups (including stale slave metadata) also exist.
    if len(groups) != 1:
        raise ValueError("Grouped snapshot has multiple group IDs; supply --group-id UUID-prefix")
    gid, members = next(iter(groups.items()))
    master = [d for d in members if d.get("role") == "master"]
    problems = []
    if len(master) != 1:
        problems.append("Expected exactly one master in the original snapshot")
    result_members = []
    for old in members:
        new = after["devices"].get(old["ip"])
        reasons = []
        if not old.get("layout_sha256"):
            reasons.append("original layout data missing")
        if not new:
            reasons.append("controller absent from target snapshot")
        else:
            if new.get("status") == "skipped":
                reasons.append("controller offline in target snapshot")
            for id_key in ("mac", "uuid", "product_code", "number_of_led"):
                a, b = old["identity"].get(id_key), new["identity"].get(id_key)
                if a is not None and b is not None and a != b:
                    reasons.append(f"{id_key} changed")
            if not new.get("layout_sha256"):
                reasons.append("target layout data missing")
        result_members.append({"name": old["name"], "ip": old["ip"],
                               "old_role": old.get("role"), "target_role": new.get("role") if new else None,
                               "same_layout": bool(new and new.get("layout_sha256") == old.get("layout_sha256")),
                               "issues": reasons})
        problems.extend(f"{old['name']}: {reason}" for reason in reasons)
    target_groups = {d.get("group_id") for d in after["devices"].values() if d["ip"] in {x["ip"] for x in members}}
    target_roles = [d.get("role") for d in after["devices"].values() if d["ip"] in {x["ip"] for x in members}]
    if any(role != "none" for role in target_roles):
        problems.append("Some target controllers still report group membership, not fully ungrouped")
    return {"source_state": before["name"], "target_state": after["name"],
            "source_snapshot": before["path"], "target_snapshot": after["path"],
            "group_id": gid, "master": master[0]["name"] if len(master) == 1 else None,
            "controllers": result_members, "issues": problems,
            "restorable_automatically": False,
            "why_not": "Area group creation and group composition restore calls are not verified",
            "safe_next_steps": [
                "Do not use POST/DELETE on layouts or grouping endpoints.",
                "Keep the grouped source snapshot and Area Editor screenshot.",
                "If ungrouped, manually recreate an Area group in Twinkly while retaining these snapshots.",
                "Capture the manually regrouped state and compare it with the original before experimenting further."
            ]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["compare", "restore"], help="restore is deliberately dry-run only")
    p.add_argument("--from-state", required=True, help="Existing grouped state, e.g. frontgroup")
    p.add_argument("--to-state", required=True, help="Current ungrouped state, e.g. ungrouped")
    p.add_argument("--group-id", help="Original group UUID or unique prefix")
    p.add_argument("--root", default="snapshots")
    p.add_argument("--output", help="Optional JSON plan output")
    args = p.parse_args()
    try:
        before = read_state(resolve(args.root, args.from_state))
        after = read_state(resolve(args.root, args.to_state))
        plan = analyse(before, after, args.group_id)
        output = json.dumps(plan, indent=2, ensure_ascii=False)
        print(output)
        if args.output:
            Path(args.output).write_text(output + "\n", encoding="utf-8")
        if args.action == "restore":
            print("\nNO DEVICE WRITES PERFORMED: automatic Area regrouping is not verified.", file=sys.stderr)
        if plan["issues"]:
            return 2
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
