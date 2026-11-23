#!/usr/bin/env python3
"""Live read-only cloud and LAN collection for Twinkly group preflight.

Cloud and LAN object/state calls use GET only. Authentication uses the
documented login/verify operations. Raw backups remain private.
"""
import datetime as dt
import os
from pathlib import Path

from cloud_audit import cloud_get, objects_from
from cloud_auth import interactive_login
from cloud_token_store import access_token as cached_access_token, store_session
from group_test import load_plan
from twinkly_inspector import authenticate as lan_authenticate
from twinkly_inspector import request, save_json, validate_ip
from cloud_group_preflight import FILES, evaluate, mac


def get_cloud_token(login=False, timeout=10):
    if login:
        session = interactive_login(timeout=timeout, return_session=True)
        token = session["access_token"]
        if session["expires_in"] > 0:
            store_session(session)
            print("Cloud session securely cached in Windows user profile.")
        return token
    token = os.environ.get("TWINKLY_CLOUD_TOKEN", "").strip() or cached_access_token()
    if not token:
        raise ValueError("Twinkly cloud session expired or missing; use --login")
    if len(token) > 4096 or any(c.isspace() for c in token):
        raise ValueError("Invalid cloud session token")
    return token


def read_local(member, timeout=5):
    ip = validate_ip(member["ip"])
    probe = request(ip, "/xled/v1/gestalt", timeout=timeout)
    if not probe.get("ok") or not isinstance(probe.get("data"), dict):
        raise RuntimeError(f"{member['name']}: cannot read controller identity")
    token, error = lan_authenticate(ip, timeout)
    if not token:
        raise RuntimeError(f"{member['name']}: LAN authentication failed")
    responses = {}
    for key, path in (("group", "/xled/v1/application/group"),
                      ("layout", "/xled/v1/led/layout/full")):
        answer = request(ip, path, token=token, timeout=timeout)
        if not answer.get("ok") or not isinstance(answer.get("data"), dict):
            raise RuntimeError(f"{member['name']}: cannot read {key}")
        responses[key] = answer["data"]
    gestalt = probe["data"]
    if not mac(gestalt.get("mac")):
        raise RuntimeError(f"{member['name']}: hardware MAC unavailable")
    if gestalt.get("number_of_led") != member["leds"]:
        raise RuntimeError(f"{member['name']}: hardware LED count differs from saved plan")
    if not isinstance(responses["layout"].get("coordinates"), list):
        raise RuntimeError(f"{member['name']}: local coordinates unavailable")
    return {"gestalt": gestalt, **responses}


def collect(plan_path, *, login=False, timeout=10, output="cloud-preflight-backups",\n            include_snapshot=False):
    plan, _ = load_plan(plan_path)
    if len(plan["members"]) != 2:
        raise ValueError("Live preflight currently supports exactly two controllers")
    if not 0 < timeout <= 30:
        raise ValueError("Timeout must be between 0 and 30 seconds")
    token = get_cloud_token(login=login, timeout=timeout)
    objects = cloud_get("/v3/objects?mine=true&fields=layout,devices,capabilities",
                        token, timeout=timeout)
    installations = cloud_get("/v3/installations", token, timeout=timeout)
    entries = objects_from(objects)
    if isinstance(objects, dict):
        if objects.get("next_page"):
            raise ValueError("Cloud object inventory has more pages; refusing incomplete data")
        if isinstance(objects.get("count"), int) and objects["count"] != len(entries):
            raise ValueError("Cloud inventory count mismatch")
    local = [read_local(m, timeout=timeout) for m in plan["members"]]
    snapshot = {FILES[0]: objects, FILES[1]: local[0], FILES[2]: local[1]}
    report = evaluate(snapshot)
    current_names = [str(x.get("name", "")) for x in report["matched_objects"]]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(output) / stamp
    if destination.exists():
        raise RuntimeError("Backup folder already exists")
    destination.mkdir(parents=True)
    for name, value in snapshot.items():
        save_json(destination / name, value)
    save_json(destination / "cloud-installations-PRIVATE.json", installations)
    save_json(destination / "preflight-report-private.json", report)
    print("Backup saved:", destination)
    print("Current cloud object names:", ", ".join(current_names) or "<none>")
    print("Keep this folder private. It may contain controller secrets.")
    return (report, plan, snapshot) if include_snapshot else (report, plan)
