# Captured Twinkly local API — Area group research

Status: **experimental, not vendor-supported**. Based on the Office two-controller traffic observed 23 November 2026, cross-checked with the read-only inspector. Examples are anonymised. Do not send commands from this document to a live group without verifying your local backup first.

## Observed firmware baseline (23 November 2026)

The provided inspector snapshot included **11 responding controllers**, and **all 11 reported firmware version 2.11.0** from `GET /xled/v1/fw/version`. The documented Office Area group create/delete protocol was observed alongside this device fleet. This does **not** establish compatibility for other firmware or controller families. Run `python probe_api.py` to produce a new per-controller firmware-indexed endpoint matrix before expanding the reference.

See the [firmware 2.11.0 compatibility matrix](firmware-2.11.0-api-matrix.md) for **31 GET endpoints tested on 11 controllers**, including observed response fields, unsupported paths and non-JSON replies. In the later 14:50 UTC capture, all controllers were on firmware 2.11.0, and Office Left/Right were **ungrouped with standalone 3D coordinates**.

## Authentication

Requests are local HTTP to each controller:
- `POST /xled/v1/login` with JSON `{"challenge":"<base64 random challenge>"}` returns `authentication_token` and `challenge-response`.
- `POST /xled/v1/verify` with `X-Auth-Token` and `{"challenge-response":"<response from login>"}`.
- Subsequent authenticated calls require `X-Auth-Token`. Token and challenge values must **never** be committed.

Twinkly's JSON `code` is the application-level status: `1000` denotes success; HTTP 200 alone is insufficient.

## Read-only endpoints observed / implemented

| Method | Path | Purpose |
|---|---|---|
| GET | `/xled/v1/gestalt` | Model, firmware family, LED count and identity |
| GET | `/xled/v1/device_name` | **Physical device name**, not Area group name |
| GET | `/xled/v1/summary` | Aggregated group mode, UID, size, offset, and layout UUID |
| GET | `/xled/v1/application/group` | Direct group membership, mode, UID, size and offset (confirmed on all 11) |
| GET | `/xled/v1/led/layout/full` | 2D/3D coordinates, source and synthesized status |
| GET | `/xled/v1/led/mode` | Active LED mode |
| GET | `/xled/v1/led/config` | String-to-LED configuration |
| GET | `/xled/v1/led/movie/config` | Movie playback config |
| GET | `/xled/v1/led/effects/current` | Current effect |
| GET | `/xled/v1/playlist/current` | Playlist state (may not return JSON) |
| GET | `/xled/v1/movies/current` | Movie state (may not return JSON) |
| GET | `/xled/v1/network/status` | Network information |
| GET | `/xled/v1/fw/version` | Firmware version |
| GET | `/xled/v1/status` | Status |

## Area group membership — captured writes

The app sent `POST /xled/v1/application/group` on **each physical controller**.

Ungroup payload:
```json
{"group":{"mode":"none","size":0,"offset":0}}
```

Regroup master:
```json
{"group":{"mode":"master","size":420,"offset":0,"uid":"<shared UUID>","compat_mode":0}}
```

Regroup slave:
```json
{"group":{"mode":"slave","size":420,"offset":210,"uid":"<same shared UUID>","compat_mode":0}}
```

The `size` represents total group LEDs; `offset` gives each controller's start position in the combined sequence. In the observed Office group both sets had 210 LEDs, so the total was 420. These numbers **must be derived and validated** for every target group, not hardcoded.

## Area LED-coordinate uploads

`POST /xled/v1/led/layout/full` with a JSON body containing `source` (`2d` or `3d`), `synthesized` and `coordinates`.

The captured app sequence restored **individual 3D layouts** when ungrouping, and uploaded **Area-wide 2D layouts** when regrouping. Accordingly, an inspected grouped snapshot contains the Area-wide layout and **is not, by itself, a full reversible backup of the original standalone camera mappings**.

## Name handling

`/xled/v1/device_name` reported “Office left” and “Office right”. The group data returned by `/xled/v1/summary` had a shared `group.uid`, roles and offsets but **no name field**. The group's friendly name may be stored by the app or cloud. Our tool's local `name` is informational and is **not sent** to `/application/group`; recreating the group may not restore its app-visible name.

## Other captured operations / limitations

Power/mode requests were observed through `/xled/v1/led/mode`. Group deletion and creation also involved effect/movie/playlist/timer traffic; the isolated group-member POSTs are **not guaranteed to cover the full app workflow**. Some endpoints may return non-JSON responses. Firmware variants may behave differently.

### Safety boundaries

- Capture and compare state of every controller before any writes.
- Never expose private IPs, authentication headers or full raw flows in a public repository.
- Always run an offline dry run before a live ungroup/regroup.
- Group operations can partially succeed; stop on a failed write and report the resulting state, do not automatically roll back or assume success.
- Test with a disposable group first. We cannot guarantee restoration of app-visible group names or effect/playlist state.

Useful upstream references: [scrool/xled](https://github.com/scrool/xled) and [REST API](https://xled-docs.readthedocs.io/en/latest/rest_api.html).
