#!/usr/bin/env python3
"""Read-only Twinkly movie retention measurements for group/ungroup/regroup.

Never downloads animation frames, writes effects or changes playback.
Compare stable movie IDs and metadata per physical MAC; report differences.
"""
import copy
from controller_workers import controller_reads
import datetime as dt

from twinkly_inspector import authenticate, request, scrub

MOVIES = "/xled/v1/movies"
PLAYLIST = "/xled/v1/playlist"
MODE = "/xled/v1/led/mode"
CURRENT = "/xled/v1/movies/current"
CONFIG = "/xled/v1/led/movie/config"
STABLE_FIELDS = ("unique_id", "name", "frames_number", "leds_per_frame",
                 "fps", "descriptor_type")


def read_member(member, timeout):
    """Authenticated GET-only snapshot; no controller state mutation."""
    token, error = authenticate(member["ip"], timeout)
    if not token:
        raise RuntimeError(member["name"] + ": auth failed before movie capture " + str(error))
    responses = {}
    for key, path in (("movies", MOVIES), ("playlist", PLAYLIST),
                      ("mode", MODE), ("current", CURRENT), ("config", CONFIG)):
        responses[key] = scrub(request(member["ip"], path, token=token, timeout=timeout))
    if not responses["movies"].get("ok"):
        raise RuntimeError(member["name"] + ": movie catalogue unavailable; refusing destructive test")
    data = responses["movies"].get("data")
    if not isinstance(data, dict) or not isinstance(data.get("movies"), list):
        raise RuntimeError(member["name"] + ": unexpected movie catalogue response")
    catalogue = []
    for obj in data["movies"]:
        if not isinstance(obj, dict):
            raise RuntimeError(member["name"] + ": malformed movie catalogue item")
        uid = obj.get("unique_id")
        if not isinstance(uid, str) or not uid.strip():
            raise RuntimeError(member["name"] + ": movie unique_id absent; cannot verify retention")
        fields = {field: copy.deepcopy(obj.get(field)) for field in STABLE_FIELDS}
        fields["id"] = obj.get("id")
        catalogue.append(fields)
    uids = [obj["unique_id"] for obj in catalogue]
    if len(set(uids)) != len(uids):
        raise RuntimeError(member["name"] + ": duplicate movie UUID in catalogue")
    return {"name": member["name"], "ip": member["ip"],
            "captured_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "movies": catalogue, "responses": responses,
            "raw_movie_frames_captured": False}


def capture(plan, output, phase, timeout, save_json):
    if len(plan["members"]) != 2:
        raise ValueError("Expected two controllers for movie retention comparison")
    records = []
    for member, result, error in controller_reads(plan["members"], lambda m: read_member(m, timeout)):
        if error is not None:
            raise error
        records.append(result)
    save_json(output / ("movie-retention-" + phase + "-private.json"),
              {"phase": phase, "controllers": records})
    print("MOVIE CHECK", phase)
    for record in records:
        print(" ", record["name"], len(record["movies"]), "movies:",
              ", ".join(str(v.get("name")) for v in record["movies"]) or "(none)")
    return records


def compare(initial, current):
    """Return per-controller loss and changes; movie ID numbers may change."""
    if len(initial) != len(current):
        raise ValueError("Controller count changed")
    report = []
    for left, right in zip(initial, current):
        if left["ip"] != right["ip"]:
            raise ValueError("Controller order or identity changed")
        expected = {movie["unique_id"]: movie for movie in left["movies"]}
        actual = {movie["unique_id"]: movie for movie in right["movies"]}
        missing = [expected[key] for key in expected.keys() - actual.keys()]
        modified = []
        for key in expected.keys() & actual.keys():
            fields = [field for field in STABLE_FIELDS
                      if expected[key].get(field) != actual[key].get(field)]
            if fields:
                modified.append({"name": expected[key].get("name"),
                                 "unique_id": key, "changed_fields": fields})
        before_playlist = left["responses"].get("playlist", {})
        after_playlist = right["responses"].get("playlist", {})
        # Compare the playlist entries only: device-reported status codes and
        # run-time selection can change without altering the actual playlist.
        if before_playlist.get("ok") and after_playlist.get("ok"):
            before_data = before_playlist.get("data", {})
            after_data = after_playlist.get("data", {})
            playlist_preserved = (
                isinstance(before_data, dict) and isinstance(after_data, dict) and
                before_data.get("entries") == after_data.get("entries")
            )
        else:
            playlist_preserved = None
        report.append({"name": left["name"], "ip": left["ip"],
                       "playlist_preserved": playlist_preserved,
                       "preserved": not missing and not modified and playlist_preserved is not False,
                       "missing": sorted(missing, key=lambda x: x["unique_id"]),
                       "modified": sorted(modified, key=lambda x: x["unique_id"]),
                       "new_count": len(actual.keys() - expected.keys()),
                       "initial_count": len(expected), "current_count": len(actual)})
    return report


def print_comparison(phase, report):
    print("MOVIE RETENTION", phase)
    for entry in report:
        print(" ", entry["name"], "PASS" if entry["preserved"] else "CHANGED",
              "initial", entry["initial_count"], "current", entry["current_count"],
              "lost", len(entry["missing"]), "modified", len(entry["modified"]),
              "playlist", entry["playlist_preserved"])
    return all(entry["preserved"] for entry in report)
