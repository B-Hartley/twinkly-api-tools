#!/usr/bin/env python3
"""Read-only Twinkly API compatibility sweep (stdlib only)."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

from twinkly_inspector import authenticate, get_devices, request, scrub, save_json

# Reviewed GET-only endpoint list. No endpoints are constructed from user input.
ENDPOINTS = {
    "gestalt": "/xled/v1/gestalt",
    "fw_version": "/xled/v1/fw/version",
    "status": "/xled/v1/status",
    "device_name": "/xled/v1/device_name",
    "summary": "/xled/v1/summary",
    "application_group": "/xled/v1/application/group",
    "application_mode": "/xled/v1/application/mode",
    "led_layout_full": "/xled/v1/led/layout/full",
    "led_mode": "/xled/v1/led/mode",
    "led_config": "/xled/v1/led/config",
    "led_movie_config": "/xled/v1/led/movie/config",
    "led_effects": "/xled/v1/led/effects",
    "led_effects_current": "/xled/v1/led/effects/current",
    "led_brightness": "/xled/v1/led/out/brightness",
    "led_color": "/xled/v1/led/color",
    "led_saturation": "/xled/v1/led/out/saturation",
    "led_movies": "/xled/v1/led/movies",
    "led_movies_current": "/xled/v1/led/movies/current",
    "led_driver_params": "/xled/v1/led/driver_params",
    "movies_current": "/xled/v1/movies/current",
    "playlist_current": "/xled/v1/playlist/current",
    "playlist": "/xled/v1/playlist",
    "timer": "/xled/v1/timer",
    "network_status": "/xled/v1/network/status",
    "network_scan_results": "/xled/v1/network/scan_results",
    "mic_config": "/xled/v1/mic/config",
    "mic_sample": "/xled/v1/mic/sample",
    "music_driver_sets_current": "/xled/v1/music/drivers/sets/current",
    "network_scan": "/xled/v1/network/scan",
    "network_station": "/xled/v1/network/station",
    "network_ap": "/xled/v1/network/ap",
    "mqtt_config": "/xled/v1/mqtt/config",
}
# Network scan is deliberately excluded even though it may be a GET:
# a scan may have side effects on Wi-Fi operations.
EXCLUDED = {"network_scan"}
PUBLIC = {"gestalt", "fw_version", "status"}


def schema(value, depth=0):
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        return {key: schema(item, depth+1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "list", "count": len(value),
                "sample": schema(value[0], depth+1) if value else None}
    return type(value).__name__


def firmware_info(gestalt, fw):
    result = {}
    for source, entry in (("gestalt", gestalt), ("fw_version", fw)):
        data = entry.get("data")
        if isinstance(data, dict):
            result[source] = {k: v for k, v in data.items()
                              if any(p in k.lower() for p in ("firmware", "version", "fw_", "product_code", "hw_"))}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", help="device JSON (default devices.json)")
    p.add_argument("--output", default="api-probes")
    p.add_argument("--timeout", type=float, default=4.0)
    args = p.parse_args()
    if args.timeout <= 0 or args.timeout > 30:
        p.error("timeout must be 0 < seconds <= 30")
    devices = get_devices(argparse.Namespace(ips=None, config=args.config))
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(args.output) / timestamp
    root.mkdir(parents=True, exist_ok=False)
    matrix = {"generated_utc": timestamp, "read_only": True, "controllers": []}
    for device in devices:
        ip = device["ip"]
        entry = {"name": device["name"], "ip": ip, "results": {}}
        base = root / "controllers" / ip.replace(".", "_")
        probe = request(ip, ENDPOINTS["gestalt"], timeout=args.timeout)
        if not probe.get("ok") and "status" not in probe:
            entry["unreachable"] = probe.get("error", "No response")
            matrix["controllers"].append(entry)
            print(f"SKIPPED {device['name']} ({ip}): {entry['unreachable']}")
            continue
        token, auth_error = authenticate(ip, args.timeout)
        entry["authentication"] = "verified" if token else auth_error
        for key, path in ENDPOINTS.items():
            if key in EXCLUDED:
                continue
            if key not in PUBLIC and not token:
                response = {"ok": False, "error": "Authentication unavailable"}
            else:
                response = (probe if key == "gestalt" else
                            request(ip, path, token=token if key not in PUBLIC else None,
                                    timeout=args.timeout))
            response = scrub(response)
            save_json(base / (key + ".json"), response)
            data = response.get("data")
            entry["results"][key] = {
                "ok": bool(response.get("ok")),
                "http_status": response.get("status"),
                "error": response.get("error"),
                "app_code": data.get("code") if isinstance(data, dict) else None,
                "schema": schema(data) if response.get("ok") else None,
            }
        responses = {key: json.loads((base / (key + ".json")).read_text(encoding="utf-8"))
                     for key in ("gestalt", "fw_version")}
        entry["firmware"] = firmware_info(responses["gestalt"], responses["fw_version"])
        matrix["controllers"].append(entry)
        working = sum(bool(x["ok"]) for x in entry["results"].values())
        print(f"{device['name']} ({ip}): {working}/{len(entry['results'])} endpoints OK; firmware={entry['firmware']}")
    save_json(root / "api-matrix.json", matrix)
    lines = ["# Twinkly API probe — local findings", "", f"Captured: {timestamp} UTC",
             "Firmware is recorded per controller. These are observations, NOT universal compatibility claims.", ""]
    for item in matrix["controllers"]:
        lines += [f"## {item['name']} ({item['ip']})", "",
                  f"Firmware: `{json.dumps(item.get('firmware', {}), ensure_ascii=False)}`", ""]
        if "unreachable" in item:
            lines += [f"Unreachable: {item['unreachable']}", ""]
            continue
        lines += ["| Endpoint | Result | Application code |", "|---|---|---|"]
        for key, value in item["results"].items():
            status = "OK" if value["ok"] else (value.get("error") or f"HTTP {value.get('http_status')}")
            lines.append(f"| GET `{ENDPOINTS[key]}` | {status} | {value.get('app_code')} |")
        lines.append("")
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved: {root}")
    print("Keep raw responses local; review for private network details before sharing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
