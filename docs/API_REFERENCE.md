# Twinkly local REST API — consolidated reference

> **Unofficial; reverse-engineered. Updated 23 November 2026.** This is the canonical API reference for observed GET/POST calls, firmware and hardware coverage, sample requests and response meanings. All identifiers and addresses in examples are fictitious. Group lifecycle operations are experimental.

## 1. Evidence and firmware/hardware versions

The 23-Nov-2026 14:50 UTC read-only sweep attempted 31 GET endpoints on **11 controllers**. Every controller returned firmware **2.11.0**. The 15:04 follow-up sweep returned different overall endpoint counts by product (usually 22/31, Icicles 16/31, Mid Right 23/31); compare its raw per-route matrix before interpreting that difference. The detailed table below refers to the **14:50** sweep.

| Product code | FW family | Hardware versions tested | Number | Firmware |
|---|---|---|---:|---|
| `TWS400SPP` | G | 100, 300, 308 | 6 | 2.11.0 |
| `TWS600SPP1` | AB | 308 | 1 | 2.11.0 |
| `TWI190SPP` | G | 100 | 1 | 2.11.0 |
| `TWP500SPP` | R | 400 | 1 | 2.11.0 |
| `TWW210SPP` | G | 300, 302 | 2 | 2.11.0 |

**Evidence grades:** `GET confirmed` means a successful JSON read in the 14:50 sweep. `POST captured` means observed in the Twinkly iPhone app's intercepted HTTP traffic. `POST tested` means replayed via Python against the two Office window controllers. `404 GET` does *not* prove that the corresponding POST is unsupported. A non-JSON reply means that our JSON-only reader could not decode it; do not interpret that as definite API absence.

**Confirmed live group test:** The Python `ungroup` operation on the Office controllers completed on 23-Nov-2026, and a subsequent independently authenticated GET returned `mode: none` on both. The later `regroup` test uploaded the Area layout to Office Left, then received **HTTP 401** on its `POST /application/group` and stopped before writing Office Right. A subsequent guarded `resume_regroup.py` run **succeeded**: Office Left `/application/group`, Office Right `/led/layout/full`, and Office Right `/application/group` each returned HTTP 200 and Twinkly JSON `code:1000`. Fresh read-back checks confirmed both controllers' saved Area layouts and expected shared group membership. This establishes a **successful local-controller ungroup/regroup round trip on this two-controller Office group with firmware 2.11.0**, not proof that the app retains its original name, playlists, or effects. Investigate current state and authentication before repeating any writes.

## 2. Base URL and authentication

Twinkly controllers are accessed on the LAN using `http://DEVICE_IP/xled/v1/<route>` (port 80). This is **HTTP**, not HTTPS. Do not expose it to the internet.

Authentication flow (POSTs observed and implemented):

```http
POST /xled/v1/login
Content-Type: application/json

{"challenge":"<base64-encoded 32 random bytes>"}
```

Successful reply includes `authentication_token` (opaque secret), `challenge-response` (opaque proof) and `code:1000`. Next:

```http
POST /xled/v1/verify
X-Auth-Token: <authentication_token>
Content-Type: application/json

{"challenge-response":"<challenge-response returned by login>"}
```

For subsequent authenticated GET and POST calls include `X-Auth-Token: <token>`. An HTTP **401** means the request was not authorised; it can indicate an expired/invalidated token, competing app session or another authentication issue. The cause in the failed Office regroup attempt has **not** been established. Do **not** retry state-changing writes just because you see 401.

Typical successful JSON response contains `"code":1000`. **HTTP 200 is not by itself proof of application success**. Unknown Twinkly application codes should be logged and treated as errors. Some endpoints can return empty or non-JSON bodies.

## 3. GET endpoint compatibility matrix (14:50 sweep)

Columns show **successful JSON / HTTP 404 / non-JSON** among 11 controllers. For a quick request example, after authentication: `GET http://DEVICE_IP/xled/v1/application/group` with `X-Auth-Token`. Endpoints here are only proven for firmware 2.11.0 and the listed hardware.

| GET path (prefix `/xled/v1/`) | OK | 404 | Non-JSON | Meaning / action |
|---|---:|---:|---:|---|
| `gestalt` | 11 | 0 | 0 | Identity, product and LED count |
| `fw/version` | 11 | 0 | 0 | Firmware version |
| `status` | 11 | 0 | 0 | Device status |
| `device_name` | 11 | 0 | 0 | Physical controller name |
| `summary` | 11 | 0 | 0 | Aggregated current state and grouping |
| `application/group` | 11 | 0 | 0 | Group membership and role |
| `application/mode` | 0 | 11 | 0 | Route absent in this GET test |
| `led/layout/full` | 11 | 0 | 0 | Complete mapped LED coordinates |
| `led/mode` | 11 | 0 | 0 | Current playback/power mode |
| `led/config` | 11 | 0 | 0 | LED string configuration |
| `led/movie/config` | 11 | 0 | 0 | Movie playback settings |
| `led/effects` | 11 | 0 | 0 | Available effects/IDs |
| `led/effects/current` | 11 | 0 | 0 | Selected effect |
| `led/out/brightness` | 11 | 0 | 0 | Brightness mode and level |
| `led/color` | 11 | 0 | 0 | RGBW and HSV color state |
| `led/out/saturation` | 11 | 0 | 0 | Saturation state |
| `led/movies` | 0 | 11 | 0 | Route absent in this GET test |
| `led/movies/current` | 0 | 11 | 0 | Route absent in this GET test |
| `led/driver_params` | 11 | 0 | 0 | LED signal timing configuration |
| `movies/current` | 1 | 0 | 10 | State-specific/non-JSON responses |
| `playlist/current` | 0 | 0 | 11 | Non-JSON; content unknown |
| `playlist` | 11 | 0 | 0 | Playlist metadata |
| `timer` | 11 | 0 | 0 | Device timer state |
| `network/status` | 11 | 0 | 0 | Connected station and local AP metadata |
| `network/scan_results` | 11 | 0 | 0 | Last known Wi-Fi scan results |
| `network/station` | 0 | 11 | 0 | Route absent in this GET test |
| `network/ap` | 0 | 11 | 0 | Route absent in this GET test |
| `mic/config` | 0 | 11 | 0 | Route absent in this GET test |
| `mic/sample` | 0 | 11 | 0 | Route absent in this GET test |
| `music/drivers/sets/current` | 11 | 0 | 0 | Current music driver set ID |
| `mqtt/config` | 11 | 0 | 0 | MQTT configuration (may include private data) |

**Total: 243 OK / 77 HTTP 404 / 21 non-JSON = 341 attempts.** `network/scan` was deliberately not probed: triggering a fresh scan might affect Wi-Fi operation.

## 4. Interpreting representative GET responses

These examples are intentionally abbreviated and/or illustrative. The **field names** were observed in the probe; values marked as examples are not literal captures. Not all fields exist on all models or in all modes.

### Hardware identification — `GET /gestalt` and `GET /fw/version`

```json
{"code":1000,"product_code":"TWW210SPP","fw_family":"G","hardware_version":"300","number_of_led":210}
```
```json
{"code":1000,"version":"2.11.0"}
```

`product_code` identifies the product family, `hardware_version` the controller hardware revision, `fw_family` the firmware family and `number_of_led` the number of LEDs physically driven by this controller. The hardware version is **not** the firmware version. The `gestalt` response may contain stable identifiers: keep raw results private.

### Names — `GET /device_name`

Returns the physical device's name. There is **no group-friendly-name field** in the observed local group response. A group named in the Twinkly app may be cached locally or held in account/cloud state; the tests do **not** establish which.

### Membership — `GET /application/group`

Ungrouped (illustrative, extra sync fields may be present):

```json
{"code":1000,"group":{"mode":"none","compat_mode":0}}
```

Grouped master:

```json
{"code":1000,"group":{"mode":"master","size":420,"uid":"00000000-0000-4000-8000-000000000001","compat_mode":0}}
```

Grouped slave:

```json
{"code":1000,"group":{"mode":"slave","size":420,"offset":210,"uid":"00000000-0000-4000-8000-000000000001","compat_mode":0}}
```

`mode=master` identifies the lead controller; `slave` follows it. `uid` must match **across all members**. `size` is the *total* group LED count, **not** each controller's count. `offset` is the starting LED index within the concatenated group sequence. Master may omit `offset`; interpret its effective offset as zero. Ungrouped devices can omit `size`, `uid`, and `offset`. Other observed group flags include `sync_on_time` and `time_sync`.

Practical Office example: Left drives LEDs 0–209, Right drives LEDs 210–419. A successful group verification must read every member and compare roles, UUID, LED counts and offsets, not merely see a group in the app.

### Aggregated summary — `GET /summary`

Observed response structure contains `code`, `group`, `layout` (with `uuid`), `led_mode`, `timer`, `music`, `filters`, and `color`. Use it for a convenient overview; prefer `GET /application/group` for authoritative local membership preflight.

### Coordinates — `GET /led/layout/full`

```json
{"code":1000,"source":"3d","synthesized":false,"uuid":"<layout-uuid>","coordinates":[{"x":0.12,"y":0.55,"z":-0.31},{"x":0.17,"y":0.61,"z":-0.25}]}
```

`source` was observed as `3d` for standalone camera mappings and `2d` for the Office Area-wide grouped layouts. This is **how the firmware labelled the data**, not a guarantee that an unused coordinate axis is always zero. `coordinates` is ordered by physical LED index, each point with x/y/z. `synthesized` describes firmware-generated mapping status; `uuid` is a layout identifier, and need not be reused when POSTing. Each controller returns *its own* coordinates; a full reversible backup requires every controller's **both** grouped and standalone layouts.

### LED mode / effects / movie / playlist

- `GET /led/mode`: current display mode; writing mode is an app-captured action, but mode-string values should be taken from a real response, not invented.
- `GET /led/config`: contains `strings[]`; useful for physical string configuration.
- `GET /led/movie/config`: playback configuration; state dependent.
- `GET /led/effects`: `effects_number` and `unique_ids[]` enumerate effects. `GET /led/effects/current` reports selection.
- `GET /movies/current`: one JSON response and ten non-JSON responses in this fleet, so inspect content type/body before attributing meanings.
- `GET /playlist`: usable JSON; `GET /playlist/current` returned non-JSON on all 11 in the archived sweep. Neither proves the presence of a playable playlist.

### Brightness, color, saturation and drivers

- `GET /led/out/brightness`: observed `code`, `mode`, `value`. Interpret `value` together with its reported `mode`, not as a fixed universal percentage without checking.
- `GET /led/color`: observed `red`, `green`, `blue`, `white`, `hue`, `saturation`, `value`; may expose both RGBW and HSV state.
- `GET /led/out/saturation`: saturation information, successful JSON on all 11.
- `GET /led/driver_params`: observed `t0h`, `t0l`, `t1h`, `t1l`, `tendh`, `tendl`; hardware signal timing; **do not alter casually**.

### Timers, networks, MQTT and music

- `GET /timer`: configured timer state. The app also made timer-related calls around grouping; no complete write contract is established.
- `GET /network/status`: contains `mode`, `station{ip,mask,gw,ssid,rssi,connected_bssid,monitor_enabled}`, `ap{ip,ssid,enc,channel,max_connections,ssid_hidden}`. Treat SSID and IP as private.
- `GET /network/scan_results`: stored scan results, **not** an instruction to initiate a scan.
- `GET /mqtt/config`: may contain `broker_host`, `broker_port`, `client_id`, `keep_alive_interval`, `user`. **Do not publish live values**.
- `GET /music/drivers/sets/current`: `driverset_id` identifies the selected driver set.
- `GET /status`: device status; specific content varies with model and current mode.

## 5. Captured POST operations

| POST path | Observed use | Evidence | Safety |
|---|---|---|---|
| `/login` | Request auth token with random challenge | App and Python | Auth-related state |
| `/verify` | Verify challenge response | App and Python | Auth-related state |
| `/application/group` | Set `none`, `master`, `slave` | Twinkly app; Python ungroup succeeded | Destructive / may partially fail |
| `/led/layout/full` | Upload camera or Area layout | Twinkly app; Python upload in regroup accepted | Replaces mapping |
| `/led/mode` | Display/power state change | Twinkly app on/off capture | Changes lights |

**Additional effect/movie/playlist/timer calls were present** around Twinkly app group changes, but their exact full payloads and sequencing are not sufficiently documented for safe reproduction here. The GET matrix must not be mistaken for a tested catalogue of write operations.

## 6. Grouping and ungrouping: exact known payloads

### Remove group membership

Send to **each** controller:

```http
POST /xled/v1/application/group
Content-Type: application/json
X-Auth-Token: <verified-token>

{"group":{"mode":"none","size":0,"offset":0}}
```

The mobile app also uploaded each controller's *standalone 3D* layout. Group removal without restoring separate mappings may leave a controller displaying the old Area coordinates. In the **23-Nov-2026 Python test**, ungroup completed on both Office devices and a fresh independent GET showed `group.mode=none` on both.

### Create the same two-controller Area group

First, the app uploaded each controller's *saved 2D Area layout*:

```http
POST /xled/v1/led/layout/full
Content-Type: application/json
X-Auth-Token: <verified-token>

{"source":"2d","synthesized":false,"coordinates":[{"x":0.1,"y":0.2,"z":0.3}]}
```

**Important:** the single coordinate above is only an example. A real Office upload must contain **210 ordered coordinates for each controller**. Do not copy the sample body onto a device.

Then the app sent group assignments on each controller with the **same** group UID:

```json
// Office Left master: illustrative UUID
{"group":{"mode":"master","size":420,"offset":0,"uid":"00000000-0000-4000-8000-000000000001","compat_mode":0}}
```

```json
// Office Right slave: same illustrative UUID
{"group":{"mode":"slave","size":420,"offset":210,"uid":"00000000-0000-4000-8000-000000000001","compat_mode":0}}
```

These are request-body examples: remove the comment lines when sending JSON. The firmware may omit master `offset` in subsequent GET output.

### First live Python regroup failure (important!)

After a successful preflight, `group_test.py regroup` performed the **Office Left** `POST /led/layout/full`, then received **HTTP 401** on `POST /application/group`; the script stopped **before** touching Office Right. The script log contains the per-step responses. A 401 is an authentication error, **not** proof the group payload is incompatible. Reasons may include token invalidation, a concurrent Twinkly-app session, or something else. Do not rerun writes until a fresh read-only status check establishes actual group modes and layout contents for both devices. A further tested write sequence is not yet available.

### Group friendly name and Twinkly app

Local group metadata contains shared UUID/role/size/offset but **no friendly name**. The app retaining an Office group tile while the controllers report `none` supports app-side persistence/caching, **but cannot distinguish local app cache from cloud/account records**. Only live group reads establish controller membership. App-visible name restoration remains unverified.

## 7. Using the Python tools (safe workflow)

From repository root on Windows:

```powershell
git pull
python .\probe_api.py
python .\twinkly_inspector.py inspect
```

Read-only GET probe writes `api-probes/<timestamp>/api-matrix.json` and `report.md`, and JSON per endpoint per controller. Inspector writes complete snapshot folders. Do not commit those private files.

For a saved plan with **both** grouped and standalone snapshots:

```powershell
python .\build_group_plan.py --grouped .\snapshots\GROUPED `
  --standalone .\snapshots\STANDALONE `
  --members "Office Left" "Office Right" `
  --name "Office Windows" --out private-area-plan.json
python .\group_test.py ungroup --plan private-area-plan.json
python .\group_test.py regroup --plan private-area-plan.json
```

These `group_test.py` invocations are **offline dry runs by default**. Live commands require a verified backup plus `--execute --acknowledge-risk --group-confirm '<group_name>'`. Because the **first live regroup failed partway**, **do not run more live writes** on this group until the state and authentication problem are assessed.

### Fiddler / HTTP clients

Use any Fiddler Composer request (or another HTTP client) with URL `http://DEVICE_IP/xled/v1/<path>`, correct HTTP verb and `X-Auth-Token` header. For POST requests supply `Content-Type: application/json` and the JSON request body. Authentication tokens are short-lived, cannot be safely hardcoded and may be invalidated by another session. **No reusable Fiddler Composer collection has yet been validated**; the snippets here are the supported manually reproducible request format.

## 8. Unknowns and testing principles

- The source snapshot tests cover only firmware **2.11.0**; other firmware versions are untested.
- The 15:04 probe's changed success counts require comparison of its full `api-matrix.json`, not speculation.
- `HTTP 401`: do not assume retry is safe after a partial sequence. Inspect each controller with fresh authentication first.
- `HTTP 404` on GET: route not present for that GET on tested firmware; not a universal API verdict.
- `JSONDecodeError`: response may be empty, binary or non-JSON; use an improved metadata probe to distinguish cases.
- Group names, cloud persistence, effects, playlists and exact Area Editor UI restoration are not fully characterised.
- Never commit private controller IPs, MAC addresses, tokens, raw flows or coordinate backups to this public repository.

Community references: [scrool/xled](https://github.com/scrool/xled) and [xled REST API](https://xled-docs.readthedocs.io/en/latest/rest_api.html).
