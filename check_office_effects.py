#!/usr/bin/env python3
"""Read-only live effect and playback inspection for the two Office controllers.

Does not change effects, group state or maps. Saves private raw replies locally.
"""
import argparse
import datetime as dt
from pathlib import Path
import sys

from group_test import load_plan
from twinkly_inspector import authenticate, request, save_json, scrub

ENDPOINTS = (
    ("mode", "/xled/v1/led/mode"),
    ("effect", "/xled/v1/led/effects/current"),
    ("movie", "/xled/v1/led/movie/config"),
    ("group", "/xled/v1/application/group"),
)


def inspect(plan, timeout, root):
    rows = []
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(root) / ("office-effects-" + stamp)
    folder.mkdir(parents=True, exist_ok=False)
    for member in plan["members"]:
        ip = member["ip"]
        token, _ = authenticate(ip, timeout)
        if not token:
            raise RuntimeError(member["name"] + ": authentication failed")
        data = {}
        for label, path in ENDPOINTS:
            reply = request(ip, path, token=token, timeout=timeout)
            if not reply.get("ok"):
                data[label] = {"error": reply.get("error", "Endpoint unavailable")}
            else:
                data[label] = reply.get("data")
        save_json(folder / (member["name"].replace(" ", "-") + "-private.json"),
                  scrub(data))
        mode = data.get("mode") or {}
        effect = data.get("effect") or {}
        movie = data.get("movie") or {}
        group = data.get("group") or {}
        role = group.get("group") if isinstance(group, dict) else {}
        led_mode = mode.get("mode") if isinstance(mode, dict) else None
        effect_uid = effect.get("unique_id") if isinstance(effect, dict) else None
        preset = effect.get("preset_id") if isinstance(effect, dict) else None
        role_mode = role.get("mode") if isinstance(role, dict) else None
        rows.append((member["name"], led_mode, preset, effect_uid, role_mode))
        print(member["name"] + ": role=" + str(role_mode) +
              ", playback=" + str(led_mode) +
              ", selected preset=" + str(preset) +
              ", effect ID=" + str(effect_uid))
        if led_mode == "off":
            print("  Selected effect is retained, but active playback is OFF.")
        if isinstance(movie, dict):
            print("  Movie frames=" + str(movie.get("frames_number")) +
                  ", delay_ms=" + str(movie.get("frame_delay")) +
                  ", sync=" + str((movie.get("sync") or {}).get("mode")))
    print("Private read-only effect snapshot:", folder)
    print("No lights or cloud state changed.")
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", default="private-area-plan.json")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--output", default="cloud-preflight-backups")
    args = p.parse_args()
    try:
        if not 0 < args.timeout <= 30:
            raise ValueError("timeout outside allowed range")
        plan, _ = load_plan(args.plan)
        if len(plan["members"]) != 2:
            raise ValueError("Expected exactly two Office members")
        inspect(plan, args.timeout, args.output)
        return 0
    except (OSError, RuntimeError, KeyError, ValueError, TypeError) as exc:
        print("BLOCK:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
