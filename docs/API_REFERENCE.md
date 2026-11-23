# Twinkly Local Controller REST API Reference

> **Unofficial, reverse-engineered protocol**, with firmware-indexed compatibility and representative request/response examples. **Confirmed** = tested GET or observed app POST, specified where relevant; **inferred** = interpretation not fully proved; **untested** = operation not verified outside the observed app. No private captures or device identifiers are included. See the separate [Cloud API Reference](CLOUD_API_REFERENCE.md).

## Transport, authentication, error handling

Base URL: `http://DEVICE_IP/xled/v1/` (LAN HTTP). Do not expose controllers directly to the internet.

1. `POST /login` with `{"challenge":"<base64-random-challenge>"}`. **Confirmed** fields in success response include `authentication_token`, `challenge-response`, `code`.
2. `POST /verify` with header `X-Auth-Token: <token>` and `{"challenge-response":"<returned-challenge-response>"}`.
3. Supply `X-Auth-Token` on subsequent authenticated calls. Some identity/status GETs can be requested without authentication.

For normal JSON success, `{"code":1000,...}` denotes application success. Check **both** HTTP status and Twinkly `code`; HTTP 200 alone is insufficient. A **401** indicates unauthorised access (token validity/session contention are possible explanations, not proven causes). A **404** on GET establishes absence of that GET route in the tested configuration, **not** absence of a corresponding POST. Non-JSON responses must not be classified as unsupported without checking status/body.

Example authenticated read:

```http
GET /xled/v1/application/group
X-Auth-Token: <token>
Accept: application/json
```

## Firmware and hardware compatibility

**Confirmed tested firmware:** `2.11.0` across **11 controllers**. No other firmware versions are verified.

| Product code | FW family | Hardware revisions tested | Controllers |
|---|---|---|---:|
| `TWS400SPP` | G | 100, 300, 308 | 6 |
| `TWS600SPP1` | AB | 308 | 1 |
| `TWI190SPP` | G | 100 | 1 |
| `TWP500SPP` | R | 400 | 1 |
| `TWW210SPP` | G | 300, 302 | 2 |
| **Total** | | | **11** |

Hardware revision is a controller hardware identifier, not a firmware version. Availability may depend on model, firmware, mode and authentication. The compatibility table below is a **fleet-wide GET result**, not guaranteed per-model capability.

### Read-only GET compatibility matrix — firmware 2.11.0

Values indicate **successful JSON / HTTP 404 / non-JSON** across the 11-device compatibility test.

| GET route (append to `/xled/v1/`) | OK | 404 | Non-JSON | Purpose |
|---|---:|---:|---:|---|
| `gestalt` | 11 | 0 | 0 | Hardware identity, model, LED count |
| `fw/version` | 11 | 0 | 0 | Firmware version |
| `status` | 11 | 0 | 0 | Device status |
| `device_name` | 11 | 0 | 0 | Physical device name |
| `summary` | 11 | 0 | 0 | Aggregated state |
| `application/group` | 11 | 0 | 0 | Group role, UID and offsets |
| `application/mode` | 0 | 11 | 0 | No GET route observed |
| `led/layout/full` | 11 | 0 | 0 | Full LED mapping |
| `led/mode` | 11 | 0 | 0 | Current LED mode |
| `led/config` | 11 | 0 | 0 | LED string configuration |
| `led/movie/config` | 11 | 0 | 0 | Playback configuration |
| `led/effects` | 11 | 0 | 0 | Effects enumeration |
| `led/effects/current` | 11 | 0 | 0 | Current effect |
| `led/out/brightness` | 11 | 0 | 0 | Brightness |
| `led/color` | 11 | 0 | 0 | Color channels |
| `led/out/saturation` | 11 | 0 | 0 | Saturation |
| `led/movies` | 0 | 11 | 0 | No GET route observed |
| `led/movies/current` | 0 | 11 | 0 | No GET route observed |
| `led/driver_params` | 11 | 0 | 0 | LED driver timing |
| `movies/current` | 1 | 0 | 10 | Current movie; often non-JSON |
| `playlist/current` | 0 | 0 | 11 | Playlist current; non-JSON |
| `playlist` | 11 | 0 | 0 | Playlist information |
| `timer` | 11 | 0 | 0 | Timer |
| `network/status` | 11 | 0 | 0 | Station/AP status |
| `network/scan_results` | 11 | 0 | 0 | Last scan results |
| `network/station` | 0 | 11 | 0 | No GET route observed |
| `network/ap` | 0 | 11 | 0 | No GET route observed |
| `mic/config` | 0 | 11 | 0 | No GET route observed |
| `mic/sample` | 0 | 11 | 0 | No GET route observed |
| `music/drivers/sets/current` | 11 | 0 | 0 | Music driver-set ID |
| `mqtt/config` | 11 | 0 | 0 | MQTT client configuration |

**Totals:** 243 valid JSON successes; 77 HTTP 404; 21 non-JSON = 341 GET attempts. `GET /network/scan` was deliberately **not** tested because it might trigger a scan. The differing success counts seen on other passes mean this matrix is observational; avoid treating it as a universal firmware contract.

## Response structures and meanings

The following examples are abbreviated and illustrative, but field names derive from observed responses.

### `GET /gestalt`, `GET /fw/version`, `GET /device_name`

```json
{"code":1000,"product_code":"TWW210SPP","fw_family":"G","hardware_version":"300","number_of_led":210}
```

```json
{"code":1000,"version":"2.11.0"}
```

`product_code` identifies hardware family; `hardware_version` hardware revision; `number_of_led` the physical controller's LED count; `device_name` returns the individual controller name, **not** an Area group's friendly name.

### `GET /application/group`

Standalone:

```json
{"code":1000,"group":{"mode":"none","compat_mode":0}}
```

Grouped master / slave:

```json
{"code":1000,"group":{"mode":"master","uid":"<group-uuid>","size":420,"compat_mode":0}}
```

```json
{"code":1000,"group":{"mode":"slave","uid":"<same-group-uuid>","size":420,"offset":210,"compat_mode":0}}
```

`mode` is `none`, `master` or `slave`. `uid` ties members together. `size` means **total group LEDs**; `offset` gives that controller's first LED in the combined sequence. A master's `offset` may be omitted and effectively equals zero. Ungrouped devices may omit UID, size and offset. Other observed flags include `sync_on_time` and `time_sync`. **Confirmed:** this GET is the strongest local indicator of physical group membership; an app tile alone is insufficient.

### `GET /led/layout/full`

```json
{
  "code":1000,
  "source":"3d",
  "synthesized":false,
  "uuid":"<layout-uuid>",
  "coordinates":[{"x":0.12,"y":0.55,"z":-0.31},{"x":0.17,"y":0.61,"z":-0.25}]
}
```

`coordinates[]` is an **ordered array per controller**, each with `x`, `y`, `z` floats. `source` is commonly `3d` for individual camera mapping and `2d` for Area/default mapped layouts; **inferred:** source alone does not establish the mapping's provenance or quality. `uuid` is layout identity and `synthesized` a firmware-provided generation flag. Validate coordinate count against `gestalt.number_of_led`, plus the full coordinate sequence (allowing modest numeric rounding) when restoring a backup.

**Confirmed:** `POST /led/layout/full` can restore both saved 2D Area and 3D standalone coordinates on firmware 2.11.0; subsequent GET verifies acceptance. Successful upload does not update cloud layout automatically. A separate app/cloud update may later overwrite displayed geometry or physical coordinates.

### `GET /summary`

Contains observed `code`, `group`, `layout{uuid}`, `led_mode`, `timer`, `music`, `filters[]` and `color`. Use as a quick overview, but use the direct group endpoint for member role validation.

### Other confirmed GET response fields

| Route | Observed fields or meaning |
|---|---|
| `led/mode` | Current LED display mode |
| `led/config` | `strings[]` |
| `led/movie/config` | Playback configuration |
| `led/effects` | `effects_number`, `unique_ids[]` |
| `led/effects/current` | Selected effect |
| `led/out/brightness` | `mode`, `value` |
| `led/color` | `red`, `green`, `blue`, `white`, `hue`, `saturation`, `value` |
| `led/out/saturation` | Saturation setting |
| `led/driver_params` | `t0h`, `t0l`, `t1h`, `t1l`, `tendh`, `tendl`; hardware signal timing |
| `movies/current` | State-dependent; non-JSON possible |
| `playlist/current` | Non-JSON on tested devices; exact body uncharacterised |
| `playlist` | Playlist metadata |
| `timer` | Timer state |
| `network/status` | `mode`, `station{ip,mask,gw,ssid,rssi,connected_bssid,monitor_enabled}`, `ap{ip,ssid,enc,channel,max_connections,ssid_hidden}` |
| `network/scan_results` | Stored Wi-Fi scan results |
| `music/drivers/sets/current` | `driverset_id` |
| `mqtt/config` | `broker_host`, `broker_port`, `client_id`, `keep_alive_interval`, `user`; may be sensitive |
| `status` | Device status (model/mode dependent) |

Never alter driver timing or MQTT/network parameters without a separate, validated procedure.

## State-changing controller calls

| Method/path | Purpose | Status |
|---|---|---|
| `POST /login`, `POST /verify` | Authenticate | **Confirmed** |
| `POST /application/group` | Set group role `none/master/slave` | **Confirmed** from app and local tests |
| `POST /led/layout/full` | Upload coordinates | **Confirmed** from app and local tests |
| `POST /led/mode` | Change mode/power state | **Observed app call**; no complete write contract here |
| Effect/movie/playlist/timer writes | Additional app operations | **Observed**, not fully characterised |

### Ungroup (each controller)

```http
POST /xled/v1/application/group
X-Auth-Token: <token>
Content-Type: application/json

{"group":{"mode":"none","size":0,"offset":0}}
```

**Confirmed:** local ungroup updates membership. To restore individual mapping, upload that controller's separately saved 3D layout. Ungrouping alone does not ensure its original camera geometry survives.

### Group (all members, using same UUID)

For example, two 210-LED controllers, first master then slave:

```json
{"group":{"mode":"master","size":420,"offset":0,"uid":"<same-group-uuid>","compat_mode":0}}
```

```json
{"group":{"mode":"slave","size":420,"offset":210,"uid":"<same-group-uuid>","compat_mode":0}}
```

**Confirmed workflow:** upload each saved Area layout with `POST /led/layout/full` before assigning its group role. Use a **fresh authentication token for each write**, then read back membership and coordinates. No automatic retry after HTTP 401 or other failed writes: a partially completed sequence needs a fresh audit first. Group member roles and 2D maps can be restored independently of the app; app/cloud object metadata remains separate.

### Upload saved layout

```http
POST /xled/v1/led/layout/full
X-Auth-Token: <token>
Content-Type: application/json

{"source":"3d","synthesized":false,"coordinates":[{"x":0.12,"y":0.55,"z":-0.31}]}
```

The example has just one point and **must not be copied to a real multi-LED device**. A real body needs the full ordered coordinates. Validate LED count, source and every coordinate before and after uploading. Layout writes can overwrite calibrated mappings; retain private backups of both individual and Area geometry.


### Area transformations and the `synthesized` flag

**Confirmed on firmware 2.11.0:** Standalone 3D layouts can switch to 2D coordinate sets when two controllers are joined into an Area, even with no manual resize. Resizing and rotation in the Area editor can change the ordered per-device coordinates and layout UUIDs while `synthesized=false` remains unchanged. Therefore, `synthesized=false` does **not** mean the mapping is an unmodified camera reconstruction, and `synthesized=true` is **not by itself** evidence of lost mapping data. Compare source, actual coordinate arrays, saved mapping UUIDs and group roles.

**Confirmed:** Ungrouping can restore previously saved standalone 3D UUIDs and coordinates; tiny float differences may occur in the recovered coordinates. **Caution:** Capturing during a group transition can yield a `master` controller paired with a controller still reporting `none`, while both already hold Area-style coordinate arrays. Treat such a state as **incomplete**, and check all members again before modifying the cloud or local configurations.

## Utilities for independent verification

```powershell
python .\probe_api.py
python .\group_status.py --plan private-area-plan.json
python .\restore_standalone.py --plan private-area-plan.json
python .\resume_regroup.py --plan private-area-plan.json
```

`probe_api.py` runs GET compatibility checks. `group_status.py` verifies live membership and layouts. The latter two default to read-only previews, requiring explicit additional flags for potentially destructive writes. See [testing and recovery procedures](testing.md).

## Known limits

- **Confirmed:** independent two-controller ungroup/regroup and standalone 3D layout restoration succeeded on the tested firmware/hardware, with full read-back of saved coordinates.
- **Inferred:** app and cloud object state can differ from the controller's active map. Local-only PUT/POST operations need not update the app's stored geometry.
- **Untested:** other firmware releases, generic app-facing name/effect restoration, a fully supported group-management API and effect/playlist round-trip restoration.
- Some GET endpoints returned non-JSON data; preserve HTTP status/content type before deciding whether an endpoint is unsupported.


## Home Assistant implementation (independent cross-check)

Home Assistant Core's built-in [Twinkly integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/twinkly) uses the [`ttls` Python client](https://pypi.org/project/ttls/) and device LAN IP address, with `iot_class: local_polling`. Its config flow asks for a **host**, not Twinkly account email/password; the integration is therefore useful for local REST auth and control examples, **not** cloud bearer-token login or remote MQTT topics. The client library handles local challenge/verify. Cloud authentication remains a separate unknown.


### Grouped effect playback validation

A confirmed, working two-controller Area was inspected while the **Scanner** movie
was running (23 November 2026). Both controllers returned `led/mode` with
`mode:"movie"`, the same movie identity/name, and `led/movie/config` with
`frames_number:80`, `frame_delay:111`, `leds_number:210`, and
`loop_type:0`. The left controller's movie configuration reported
`sync.mode:"master"` and `sync.master_id`; the right reported
`sync.mode:"slave"` and `sync.slave_id`. These two IDs were the same
UUID after case normalization. Their separate group roles also agreed:
left master, right slave, total size 420 and right offset 210.

**Recommended read-only validation when a grouped movie is running:**
compare `GET /xled/v1/application/group` roles/UUID/size/offset;
`GET /xled/v1/led/mode` movie identities; and
`GET /xled/v1/led/movie/config` movie timing, frame count,
per-controller LED count, and synchronization mode/UUID (compare IDs as
canonical UUIDs). The movie `sync.master_id` and `sync.slave_id`
fields must not be confused with individual device IDs or assumed to
match the cloud object ID. The captured sync IDs matched the group UUID
in this working example, but behavior across other firmwares is not
yet established. **An API match alone does not prove physical timing;**
visual confirmation of both windows operating together remains useful.
No private identifiers are included in this reference.
