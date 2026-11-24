#!/usr/bin/env python3
"""Read-only Twinkly Area-group inspector. Python 3.10+, stdlib only."""
import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import sys
import urllib.error
import urllib.request

ENDPOINTS = {
    "gestalt": "/xled/v1/gestalt",
    "firmware": "/xled/v1/fw/version",
    "status": "/xled/v1/status",
    "device_name": "/xled/v1/device_name",
    "summary": "/xled/v1/summary",
    "led_layout_full": "/xled/v1/led/layout/full",
    "led_movie_config": "/xled/v1/led/movie/config",
    "led_mode": "/xled/v1/led/mode",
    "led_config": "/xled/v1/led/config",
    "current_effect": "/xled/v1/led/effects/current",
    "effects_catalogue": "/xled/v1/led/effects",
    "movies_catalogue": "/xled/v1/movies",
    "playlist": "/xled/v1/playlist",
    "playlist_current": "/xled/v1/playlist/current",
    "movies_current": "/xled/v1/movies/current",
    "network_status": "/xled/v1/network/status",
    "mqtt_config": "/xled/v1/mqtt/config",
}
PUBLIC = {"gestalt", "firmware", "status"}
SENSITIVE_KEYS = {"authentication_token", "challenge-response", "challenge", "token",
                  "password", "secret", "mqtt_password", "pass", "passwd", "encryption_key"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def scrub(value):
    if isinstance(value, dict):
        return {key: ("<redacted>" if key.lower() in SENSITIVE_KEYS else scrub(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def request(ip, path, *, method="GET", token=None, payload=None, timeout=5):
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"http://{ip}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Response exceeds 8 MiB safety limit")
            result = json.loads(raw)
            return {"ok": result.get("code") in (None, 1000) if isinstance(result, dict) else True,
                    "status": response.status, "data": result}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def authenticate(ip, timeout):
    challenge = base64.b64encode(os.urandom(32)).decode("ascii")
    login = request(ip, "/xled/v1/login", method="POST", payload={"challenge": challenge}, timeout=timeout)
    if not login.get("ok") or not isinstance(login.get("data"), dict):
        return None, {"login": login.get("error", login.get("status", "failed"))}
    data = login["data"]
    token = data.get("authentication_token")
    response = data.get("challenge-response")
    if not token or not response:
        return None, {"login": "Missing authentication token/challenge-response"}
    verified = request(ip, "/xled/v1/verify", method="POST", token=token,
                       payload={"challenge-response": response}, timeout=timeout)
    if not verified.get("ok"):
        return None, {"verify": verified.get("error", verified.get("status", "failed"))}
    return token, {}


def validate_ip(ip):
    address = ipaddress.ip_address(ip)
    if not address.is_private:
        raise ValueError(f"{ip}: only private LAN IP addresses are accepted")
    if address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local:
        raise ValueError(f"{ip}: not a usable device address")
    if address.version != 4:
        raise ValueError("Only IPv4 Twinkly controllers are supported")
    return str(address)


def get_devices(args):
    # Explicit CLI IPs are the entire selection; never merge them with
    # devices.json (even when that file contains the same IP addresses).
    if getattr(args, "ips", None):
        devices = [{"ip": ip, "name": ip} for ip in args.ips]
        config_path = None
    else:
        devices = []
        config_path = Path(args.config) if args.config else Path("devices.json")
    if config_path is not None and args.config and not config_path.is_file():
        raise ValueError(f"Configuration file not found: {config_path}")
    if config_path is not None and config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("devices"), list):
            raise ValueError("Configuration must contain a devices list")
        for d in config["devices"]:
            if not isinstance(d, dict) or not isinstance(d.get("ip"), str):
                raise ValueError("Each device must contain an ip string")
            devices.append({"ip": d["ip"], "name": str(d.get("name", d["ip"]))})
    if not devices:
        raise ValueError("No controllers configured. Supply --ips or a valid devices.json.")
    by_ip = {}
    for d in devices:
        d["ip"] = validate_ip(d["ip"])
        if d["ip"] in by_ip:
            raise ValueError(f"Duplicate controller IP: {d['ip']}")
        by_ip[d["ip"]] = d
    return devices


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def inspect(args):
    devices = get_devices(args)
    now = dt.datetime.now(dt.timezone.utc)
    group_name = getattr(args, "group_name", None)
    if group_name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", group_name):
        raise ValueError("--group-name must use letters, digits, hyphen, underscore (max 64)")
    base = Path(args.output) / group_name / now.strftime("%Y%m%dT%H%M%SZ") if group_name else Path(args.output) / now.strftime("%Y%m%dT%H%M%SZ")
    if base.exists():
        raise ValueError(f"Snapshot exists: {base}; rerun later")
    base.mkdir(parents=True)
    manifest = {"schema": 1, "state_name": group_name, "captured_utc": now.isoformat(), "purpose": "Read-only inspection: movie metadata, playlist and current selections only; NOT a restorable raw movie backup",
                "controllers": []}
    skipped = []
    report = ["Twinkly Area Group Inspector", f"Captured UTC: {now.isoformat()}",
              "WARNING: This snapshot does NOT prove Area Editor position/rotation/scale can be restored.", ""]
    for d in devices:
        ip, name = d["ip"], d["name"]
        folder = base / "controllers" / ip.replace(".", "_")
        details = {"ip": ip, "name": name, "endpoints": {}, "errors": []}
        probe = request(ip, ENDPOINTS["gestalt"], timeout=args.timeout)
        if not probe.get("ok") and "status" not in probe:
            reason = probe.get("error", "Device unreachable")
            details.update({"status": "skipped", "skip_reason": reason, "authentication": "not_attempted"})
            details["errors"].append({"connection": reason})
            manifest["controllers"].append(details)
            skipped.append((name, ip, reason))
            report.extend([f"{name} ({ip})", f"  SKIPPED: {reason}", ""])
            print(f"SKIPPED {name} ({ip}): {reason}")
            continue
        details["status"] = "inspected"
        token, auth_error = authenticate(ip, args.timeout)
        details["authentication"] = "verified" if token else "failed"
        if auth_error:
            details["errors"].append({"authentication": auth_error})
        for label, path in ENDPOINTS.items():
            if label not in PUBLIC and not token:
                result = {"ok": False, "error": "Authentication unavailable"}
            else:
                result = probe if label == "gestalt" else request(ip, path, token=token if label not in PUBLIC else None, timeout=args.timeout)
            result = scrub(result)
            save_json(folder / "responses" / (label + ".json"), result)
            entry = {"ok": result["ok"]}
            if result.get("status") is not None:
                entry["http_status"] = result["status"]
            if result["ok"]:
                entry["sha256"] = digest(result["data"])
            else:
                entry["error"] = result.get("error", "API returned error code")
                details["errors"].append({label: entry["error"]})
            details["endpoints"][label] = entry
        gestalt_file = folder / "responses" / "gestalt.json"
        gestalt = json.loads(gestalt_file.read_text(encoding="utf-8")).get("data", {})
        if not isinstance(gestalt, dict):
            gestalt = {}
        details["identity"] = {key: gestalt.get(key) for key in
                               ("device_name", "product_code", "fw_family", "number_of_led", "uuid", "mac") if key in gestalt}
        layout = json.loads((folder / "responses" / "led_layout_full.json").read_text(encoding="utf-8"))
        data = layout.get("data") if layout.get("ok") else None
        if isinstance(data, dict):
            details["layout_keys"] = sorted(data.keys())
            for key in ("coordinates", "leds", "layout"):
                if isinstance(data.get(key), list):
                    details["coordinate_count"] = len(data[key])
                    break
        mqtt_entry = details["endpoints"].get("mqtt_config", {})
        mqtt_file = folder / "responses" / "mqtt_config.json"
        mqtt_reply = json.loads(mqtt_file.read_text(encoding="utf-8"))
        mqtt_info = mqtt_reply.get("data") if mqtt_entry.get("ok") else None
        if isinstance(mqtt_info, dict):
            details["mqtt_summary"] = {
                "available": True,
                "host": mqtt_info.get("broker_host"),
                "port": mqtt_info.get("broker_port"),
                "tls_expected": mqtt_info.get("broker_port") == 8883,
                "username_supplied": bool(mqtt_info.get("user")),
                "password_field_present": "password" in mqtt_info,
                "authentication_unknown": True,
                "connection_not_tested": True,
            }
        else:
            details["mqtt_summary"] = {
                "available": False, "connection_not_tested": True,
                "authentication_unknown": True,
            }
        movie_catalogue = details["endpoints"].get("movies_catalogue", {})
        details["effects_capture"] = {
            "movie_catalogue_retrieved": bool(movie_catalogue.get("ok")),
            "playlist_retrieved": bool(details["endpoints"].get("playlist", {}).get("ok")),
            "effect_catalogue_retrieved": bool(details["endpoints"].get("effects_catalogue", {}).get("ok")),
            "selected_movie_retrieved": bool(details["endpoints"].get("movies_current", {}).get("ok")),
            "raw_movie_frames_retrieved": False,
            "raw_movie_frames_reason": "No documented read-only movie-frame download API; the documented /movies/full endpoint is upload-only.",
            "fully_restorable_movie_backup": False,
        }
        manifest["controllers"].append(details)
        report.extend([f"{name} ({ip})",
                       f"  Device: {details['identity']}",
                       f"  Authentication: {details['authentication']}",
                       f"  Layout: {'OK' if layout.get('ok') else 'UNAVAILABLE'}; "
                       f"keys={details.get('layout_keys', [])}; "
                       f"coordinate_count={details.get('coordinate_count', 'unknown')}",
                       f"  Endpoints OK: {sum(e['ok'] for e in details['endpoints'].values())}/{len(ENDPOINTS)}",
                       f"  Errors: {details['errors']}", ""])
        print(f"{name} ({ip}): {sum(e['ok'] for e in details['endpoints'].values())}/{len(ENDPOINTS)} endpoints")
    inspected = [c for c in manifest["controllers"] if c["status"] == "inspected"]
    complete = [c for c in inspected if c["endpoints"]["led_layout_full"]["ok"]]
    manifest["summary"] = {"configured": len(devices), "inspected": len(inspected),
                           "layout_retrieved": len(complete), "skipped": len(skipped)}
    summary = ["RUN SUMMARY",
               f"Configured: {len(devices)} | Inspected: {len(inspected)} | "
               f"Layouts retrieved: {len(complete)} | Skipped: {len(skipped)}"]
    for name, ip, reason in skipped:
        summary.append(f"  SKIPPED {name} ({ip}): {reason}")
    for c in inspected:
        if not c["endpoints"]["led_layout_full"]["ok"]:
            summary.append(f"  WARNING {c['name']} ({c['ip']}): LED layout unavailable")
    report.extend(summary)
    save_json(base / "manifest.json", manifest)
    (base / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n" + "\n".join(summary))
    print(f"Saved snapshot: {base}")
    return 0 if complete else 2


def compare(args):
    a = json.loads((Path(args.before) / "manifest.json").read_text(encoding="utf-8"))
    b = json.loads((Path(args.after) / "manifest.json").read_text(encoding="utf-8"))
    aa = {c["ip"]: c for c in a["controllers"]}
    bb = {c["ip"]: c for c in b["controllers"]}
    for ip in sorted(aa.keys() | bb.keys()):
        if ip not in aa or ip not in bb:
            entry = bb.get(ip, aa.get(ip))
            print(f"{entry.get('name', ip)} ({ip}): {'ADDED' if ip in bb else 'REMOVED'}")
            continue
        changed, unavailable = [], []
        for name in ENDPOINTS:
            x = aa[ip]["endpoints"].get(name, {})
            y = bb[ip]["endpoints"].get(name, {})
            if not x.get("ok") or not y.get("ok"):
                unavailable.append(name)
            elif x.get("sha256") != y.get("sha256"):
                changed.append(name)
        name = bb[ip].get("name") or aa[ip].get("name") or ip
        print(f"{name} ({ip}): changed={changed or 'none'}; unavailable={unavailable or 'none'}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("inspect", help="Collect safe, read-only API snapshots")
    i.add_argument("--ips", nargs="+", help="LAN IPv4 addresses of every physical Area group controller")
    i.add_argument("--config", help="JSON file with devices; defaults to ./devices.json")
    i.add_argument("--output", default="snapshots", help="Local snapshot root")
    i.add_argument("--group-name", help="Named state such as frontgroup or ungrouped")
    i.add_argument("--timeout", type=float, default=5, help="Per-request timeout (seconds)")
    c = sub.add_parser("compare", help="Compare endpoint hashes of two snapshots")
    c.add_argument("before")
    c.add_argument("after")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            if not 0.1 <= args.timeout <= 30:
                parser.error("--timeout must be between 0.1 and 30 seconds")
            return inspect(args)
        return compare(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
