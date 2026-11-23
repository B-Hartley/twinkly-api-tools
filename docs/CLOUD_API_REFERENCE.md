# Twinkly Cloud API Reference

> **Unofficial, reverse-engineered API.** This page documents protocol behaviour, not individual captures or installations. **Confirmed** means observed in successful app requests/responses; **inferred** means a reasonable interpretation not independently proved; **untested** means no compatible external client write has been verified. The endpoints and structures may change.

The cloud service at `https://api.twinkly.com` stores account, installation and object records. It is **separate** from the LAN controller API at `http://DEVICE_IP/xled/v1/`. See [Local API Reference](API_REFERENCE.md).

## Authentication and response handling

- **Confirmed:** Cloud requests use HTTPS with `Authorization: Bearer <access-token>`. This is **not** the LAN `X-Auth-Token` mechanism.
- **Confirmed:** Successful cloud responses use conventional HTTP status codes such as **200 OK**, **201 Created** and **204 No Content**. Unlike the local controller API, a Twinkly JSON `code:1000` field is not required for cloud calls.
- **Confirmed:** `POST /v2/auth` accepts JSON `username` and `password` and responds with `access_token`, `refresh_token`, `expires_in`, `token_type` (HTTP 200). The included interactive `cloud_auth.py` implements this exchange but has **not yet been tested against a live account**. **Untested:** token refresh endpoint and renewal. Do not hard-code or commit credentials. Cloud payloads may contain account, installation and device credential fields; keep complete responses private.

## Endpoint index

| Method | Relative path | Purpose | Evidence |
|---|---|---|---|
| POST | `/v2/auth` | Authenticate with JSON `username` and `password`; receive bearer and refresh tokens | **Confirmed** 200 |
| PUT | `/v2/users/me` | Update user profile and preferences | **Confirmed** 200 |
| GET | `/v2/users/me` | Get authenticated account information | **Confirmed** 200 |
| GET | `/v3/installations` | Get installations and their object references | **Confirmed** 200 |
| GET | `/v4/effects/source` | Retrieve effect source definitions | **Confirmed** 200 |
| GET | `/v3/objects` | Get saved light objects; supports query selectors | **Confirmed** 200; conditional request can return **304** |
| GET | `/v3/runtime/objects/{objectId}` | Retrieve per-object runtime information | **Confirmed** 200; full response fields not yet characterised |
| POST | `/v3/objects` | Create a saved light object | **Confirmed** 201 |
| PUT | `/v3/objects/{objectId}` | Update an *existing* object, including layout | **Confirmed** 200 |
| PUT | `/v3/installations/{installationId}/objects` | Modify installation-object association | **Confirmed** 200 for `action:"remove"` |
| POST | `/v3/reconstruction` | Submit calibration/reconstruction input for processing | **Confirmed** 201 |
| GET | `/v3/reconstruction/{id}` | Obtain reconstruction job result and status | **Confirmed** 200 |
| GET | `/v3/reconstruction/snapshot/{uuid}` | Look up saved reconstruction snapshot | **Endpoint observed**, response can be 404 |
| DELETE | `/v3/reconstruction/snapshot/{uuid}` | Snapshot cleanup | **Endpoint observed**, success not established |
| DELETE | `/v3/effects/compiled/{uuid}` | Compiled effect cleanup | **Confirmed** 204; mapping dependency **unproven** |

## Shared installations and remote runtime

**Confirmed:** a signed-in account with access to a shared installation can read `GET /v3/installations` and fetch object runtime information through `GET /v3/runtime/objects/{objectId}`. These are distinct from the saved-object definition returned by `/v3/objects`. The precise response schema and how remote authorisation is enforced have not been fully verified. `GET /v3/objects` may return HTTP **304 Not Modified** when conditional caching headers are supplied; a 304 without a response body must not be treated as an empty object list.

**Remote access / MQTT:** the local `/xled/v1/mqtt/config` route is readable on tested controllers, but **no MQTT broker connection or subscription is demonstrated by HTTPS proxy traces alone**. A packet capture that includes MQTT traffic, device-side MQTT configuration or official protocol documentation would be needed to verify the broker, topics and credentials. Do not assume `/v3/runtime/objects` itself is MQTT.

**Cloud login:** confirmed `POST /v2/auth` with JSON `username`/`password`, returning `access_token`, `refresh_token`, `expires_in`, `token_type`. The token-refresh request is **not** established. Device `/xled/v1/login` and `/verify` exchanges are **LAN authentication**, not cloud account authentication. A preauthenticated or cached app session can make the login exchange invisible in an HTTPS proxy recording.


### Observed object lifecycle when grouping and ungrouping

**Confirmed by before/during/after authenticated cloud inventory:** joining two standalone, 210-LED objects into an Area can remove the two old object IDs from the returned inventory and introduce a newly allocated, 420-LED Area object with a 2D layout. Ungrouping can remove that Area object and create **new individual object IDs**, each representing 210 LEDs with a 3D layout. Therefore, any automation **must not assume stable object IDs across group operations**, and should re-discover objects and installation membership after each change.

**Confirmed by corresponding controller readbacks:** the local controllers transitioned `none → master/slave → none`, with `3d → 2d → 3d` mapping sources. End-of-cycle coordinate fingerprints matched the beginning for both members; that is stronger evidence of recovery than matching an older saved plan.

**Confirmed protocol from a complete intercepted app join/unjoin sequence:** The client sends `POST /v3/objects` with the two controller descriptors and combined 2D layout and receives **201**, without a separate observed removal request for the two old standalone objects at join time. During unjoin, it sends `PUT /v3/installations/{installationId}/objects` with `action:"remove"` and the Area object ID, receiving **200**; it then sends two separate `POST /v3/objects` requests, each describing one controller with its standalone 3D layout, and receives **201** for each. A later second join also used `POST /v3/objects` with two devices and a 2D layout. **Inferred:** the cloud service reconciles or hides preexisting individual objects when creating a new Area; exact deletion, ownership, and rollback semantics remain unverified. Do not separately delete old objects during join on the assumption that a removal call is required.

## Objects and mappings

Cloud objects model individual controllers or compound Area installations. **Confirmed** object fields in creation and update requests include:

| Field | Meaning |
|---|---|
| `name` | Human-readable object/group name |
| `uuid` | Object identifier distinct from URL numeric `objectId` |
| `installationId` | Installation association, where supplied |
| `devices[]` | Members; nested `part` and `device` entries |
| `ledCount` | Total LEDs represented by the object |
| `ledProfile`, `fps`, `capacity` | Device/playback capabilities |
| `meta`, `palette` | Object metadata and palette |
| `layout` | Mapping description and encoded coordinates |

Some fields appear in only certain request types. **Do not assume every field is optional.** Device descriptors can include private credentials and identifiers; never copy actual `devices[]` payloads to documentation or public logs.

### Layout schema

**Confirmed** fields inside `layout`:

| Field | Meaning |
|---|---|
| `source` | `2d` or `3d` mapping source |
| `coords` | **Base64-encoded, GZIP-compressed JSON coordinate array** |
| `geometry` | Mapping geometry classification |
| `uuid` | Layout UUID |
| `name` | Layout name |
| `synthesized` | Whether marked as synthesised |
| `aspectXY`, `aspectXZ` | Layout aspect metadata |

The decoded coordinate array contains ordered objects with numeric `x`, `y`, `z` fields. Its length must equal the object LED count. **Confirmed:** 3D layouts can represent standalone devices; 2D layouts can represent Area/combined or default arrangements. **Inferred:** `source` alone is insufficient to distinguish a calibrated Area layout from a default grid.

Read-only decoding example:

```python
import base64
import gzip
import json

coords = json.loads(gzip.decompress(base64.b64decode(obj["layout"]["coords"])))
assert len(coords) == obj["ledCount"]
print("LEDs:", len(coords), "first point:", coords[0])
```

To produce equivalent data for an authorised update, the inverse is `base64.b64encode(gzip.compress(json.dumps(coords).encode("utf-8"))).decode("ascii")`. Exact request-body compatibility with independent clients remains **untested**.

### GET objects and installations

```http
GET /v3/objects?mine=true
Authorization: Bearer <access-token>
```

**Confirmed:** the ordinary object-list request can omit mapping and device details. Request `fields=layout,devices,capabilities` with `mine=true` to obtain detailed object records; a request without `fields` may return objects lacking `layout` and `devices`. Other selectors include `objectIds` (combinations and pagination not exhaustively characterised). `GET /v3/installations` retrieves installation metadata. **Inferred:** object records are an important source for the app's inventory and displayed maps; app-specific caching and refresh behaviour has not been proven.


### Cloud Area creation / removal protocol

**Confirmed application requests (not yet independently replay-tested):**

| Step | Cloud operation | Request body / outcome |
|---|---|---|
| Join | `POST /v3/objects` | Full object definition, `installationId`, `devices[]` containing both controllers, `ledCount` for the combined Area, `layout.source: "2d"`, compressed `layout.coords`; HTTP **201** |
| Unjoin | `PUT /v3/installations/{installationId}/objects` | `{"action":"remove","objectIds":[<area-object-id>]}`; HTTP **200** |
| Unjoin | `POST /v3/objects` for controller A | Full single-controller object definition, `layout.source: "3d"`; HTTP **201** |
| Unjoin | `POST /v3/objects` for controller B | Full single-controller object definition, `layout.source: "3d"`; HTTP **201** |

The **confirmed create request keys** are `uuid`, `name`, `ledCount`, `capacity`, `fps`, `ledProfile`, `installationId`, `devices`, `layout`, and `meta`. Within `layout`, confirmed keys are `source`, `coords`, `geometry`, `uuid`, `aspectXY`, `aspectXZ`, `name` and `synthesized`. A valid request must preserve the correct private per-device descriptors and membership; copying literal device credentials from documentation is never appropriate.

**Confirmed local operation order in a complete join:** the app uploads 2D layouts to individual controllers before sending master/slave group-role updates; the cloud Area object creation follows. **Confirmed local operation order on unjoin:** the app sets both controllers' group role to `none`, uploads 3D layouts, then removes the cloud Area association and creates two individual cloud objects.

**Not established:** atomicity or rollback across LAN/cloud, whether old cloud records are deleted or merely detached, what happens if one creation fails, and how to achieve cross-account consistency. Cloud object IDs can change on every join/unjoin. Do not automatically retry an HTTP 201 create on timeout: duplicate objects may result. An authorised independent client must first back up cloud and local state, verify object/device identity and entire layouts, and stop on an ambiguous write outcome.

### Create an object — POST

```http
POST /v3/objects
Authorization: Bearer <access-token>
Content-Type: application/json
```

**Confirmed:** This creates a new object (HTTP **201**). The body contains object metadata, membership and layout. A created response can include `id`, `ownerId`, `installation`, `users[]`, `devices[]`, `layout`, `created`, and `updated`. **Do not use POST to update an existing object**, as duplicates may result.

### Update an existing object — PUT

```http
PUT /v3/objects/{objectId}
Authorization: Bearer <access-token>
Content-Type: application/json
```

**Confirmed:** PUT to the existing object's ID can update its map and returns HTTP **200**. Observed request keys: `name`, `uuid`, `ledCount`, `devices`, `ledProfile`, `fps`, `capacity`, `meta`, `palette`, `layout`. In particular the new `layout.coords`, `layout.source` and layout metadata are sent with the object's other fields. A cloud object need not be deleted or recreated for a mapping change.

**Untested:** Minimal layout-only PUT, optimistic concurrency, response semantics after external-client writes, and what app refresh does after such a write. An updater should first read and privately back up the complete current object, preserve immutable/ownership fields and membership, verify the target ID, then perform a separately authorised test. The repository now provides an **experimental guarded single-object 3D layout PUT** with private backup and GET readback; see below. Cloud group create/remove and 2D→3D metadata conversion are not implemented.

### Installation membership — PUT

```http
PUT /v3/installations/{installationId}/objects
Authorization: Bearer <access-token>
Content-Type: application/json

{"objectIds":[123456],"action":"remove"}
```

The ID above is fictitious. **Confirmed:** `action:"remove"` returns HTTP **200**. **Untested:** Other actions, whether this deletes the underlying object, and effects on other members. Treat as potentially destructive.

## Reconstruction / camera-mapping workflow

**Confirmed** general protocol:

```text
multipart reconstruction input
      |
      v
POST /v3/reconstruction  -> 201, job id and status
      |
      v
GET /v3/reconstruction/{id} -> 200, job status and output
      |
      v
POST /xled/v1/led/layout/full -> local controller accepts coordinates
      |
      v
PUT /v3/objects/{objectId} -> cloud object stores encoded layout
```

### Submit reconstruction

`POST /v3/reconstruction` uses **`multipart/form-data`**, not an ordinary JSON request. **Confirmed** multipart field names include `file` (one or more parts), `feedback`, `feedbackValidGapSize`, `uuid`. Different requests have different field combinations; not all are universally required.

Successful JSON response example *shape* (identifiers redacted):

```json
{
  "id": "<job-id>",
  "status": "<job-status>",
  "input": {
    "threads": "<value>",
    "feedback": "<value>",
    "rect2d": "<value>",
    "floor": "<value>",
    "uuid": "<value>",
    "parallelViewsFilter": "<value>",
    "outliersRecovering": "<value>"
  },
  "output": {},
  "created": "<timestamp>",
  "updated": "<timestamp>"
}
```

This illustrates observed **field names**, not tested literal value types or a valid POST body. Input options may include `feedbackValidGapSize` depending on the operation. **Inferred:** each submission can incrementally generate mapping information from captured scan inputs; the server's exact reconstruction algorithm and required number of scans are not established.

### Fetch reconstruction

`GET /v3/reconstruction/{id}` returns job metadata and can yield `status:"succeeded"` plus `output.data` and `output.executionTime`. **Confirmed:** at least some successful response bodies are GZIP-compressed, so clients must decode content encoding before JSON parsing. The structure and meaning of `output.data` should be validated before turning it into an LED map; do not equate an HTTP 200 with successful calibration.

### Apply resulting map

**Confirmed:** the app can upload the selected mapping to the LAN with `POST /xled/v1/led/layout/full` (JSON coordinates), then persist an encoded version in the cloud using `PUT /v3/objects/{objectId}`. Cloud and LAN writes are separate and may succeed independently. Consult the [Local API Reference](API_REFERENCE.md) for controller-side fields and validation.

## Confirmed vs inferred vs untested

- **Confirmed:** cloud create, existing-object update, installation-object removal, reconstruction submission/retrieval, encoded `layout.coords`, and separate local mapping upload.
- **Inferred:** stale cloud or app-cached geometry may explain why app previews differ from live controller mappings.
- **Untested:** cloud token refresh, independently replaying the cloud Area create/remove cycle, independent programmatic object PUT, app refresh semantics, deletion/rollback behaviour, and reconstruction parameters for arbitrary devices.
- **Safety:** compare target object identity and decoded geometry before any write; never publish raw captures, object credentials, access tokens or identifying values.

The cloud API is **not documented or guaranteed by Twinkly**. Use it at your own risk.

## Read-only cloud inventory and login research

The repository includes **`cloud_audit.py`**, a strictly allowlisted HTTPS GET client for `/v3/objects?mine=true&fields=layout,devices,capabilities` and `/v3/installations`. Its inventory calls are GET-only; authentication performs POST `/v2/auth`, and it does not implement cloud object/installation writes and never sends your cloud token to another hostname. The code is experimental: cloud object-list response shapes are not completely established.

The captured login contract is implemented for **interactive local use**. The new `--login` command saves a Windows DPAPI-encrypted access/refresh token cache after successful login; the password is never saved. For a read-only inventory of installations and objects, run:

```powershell
git pull
python .\cloud_audit.py --plan private-area-plan.json --login
```

You will be prompted for your email and password (password entry is hidden). The implementation is **not yet live-account-tested**, and no cloud changes are made beyond the authentication POST. Alternatively, if you already have a legitimate short-lived bearer token, set it in the **current PowerShell process only** without echoing the value:

```powershell
$secret = Read-Host "Twinkly cloud access token" -AsSecureString
$env:TWINKLY_CLOUD_TOKEN = [System.Net.NetworkCredential]::new("", $secret).Password
python .\cloud_audit.py --plan private-area-plan.json
Remove-Item Env:TWINKLY_CLOUD_TOKEN
```

The script writes **private account/device object backups** to `cloud-audits/<timestamp>/`. Those backups are ignored by Git and **must not** be shared publicly. The audit's inventory requests are GET-only; interactive authentication performs **one POST to `/v2/auth`**, not an object/installation mutation. It stores login tokens only in Windows DPAPI-encrypted form under `%LOCALAPPDATA%/TwinklyApiTools/cloud-session.dpapi`. Successful login requires valid account credentials and access to the relevant installation.

### Capture login flow safely

To discover authentication endpoints without exposing your password or tokens, save a new app login mitmproxy `.flows` recording **locally**, then run:

```powershell
python .\redact_login_trace.py C:\TwinklyCapture\twinkly-login.flows --output C:\TwinklyCapture\twinkly-login-redacted.json
```

This reports **only** the HTTP method, hostname-filtered path (without query), response status, content-type-derived JSON **key names**, and whether an authorization header is present; it never prints token values, passwords, request/response JSON values, headers or cookies. **Review even the redacted file before sharing**, since a URL path or JSON key name may still disclose information. The `.flows` source is sensitive and should not be uploaded or committed.

**Next prerequisites for cloud sync writes:** verify live login and refresh behaviour, identify cloud object IDs for the intended controllers, validate complete PUT/create/remove payload schemas, back up all relevant cloud records, and test guarded writes against a disposable group with explicit approval. A cloud/physical atomic transaction cannot be assumed; partial failures must be reported rather than blindly rolled back.

### Token persistence and expiry (Windows)

The first `--login` prompts for account email and a hidden password, uses `POST /v2/auth` and encrypts the returned access and refresh tokens using **Windows DPAPI tied to the current Windows account**. It stores the encrypted data at `%LOCALAPPDATA%\\TwinklyApiTools\\cloud-session.dpapi`; neither the password nor plaintext tokens are saved. Subsequent runs without `--login` reuse the access token **until shortly before expiry**.

```powershell
# Initial interactive login
python .\\cloud_audit.py --plan private-area-plan.json --login
# Repeat audit without typing credentials, while token valid
python .\\cloud_audit.py --plan private-area-plan.json
# Clear saved access/refresh tokens
python .\\cloud_audit.py --plan private-area-plan.json --forget-login
```

**Important limitation:** the API endpoint and payload for redeeming `refresh_token` have **not yet been verified**. We save it encrypted for later support, but **cannot renew access indefinitely yet**. When the access token expires, you must run `--login` again. The new encrypted cache workflow remains untested on a live Windows account. Do not use plaintext password JSON or commit any token files.

## Experimental cloud layout update tool

`cloud_layout_update.py` implements one potential cloud write: replacing an existing **3D single-controller object's** encoded coordinates with those from a verified private saved plan. It uses the DPAPI-cached bearer token from `cloud_audit.py --login`, fetches the exact target object, checks its single device's name and LED count, saves a private before-object backup and the complete proposed request, and performs a second preflight GET before writing. The request uses the observed `PUT /v3/objects/{objectId}` route and reads back coordinates after the response. **The script has not yet been verified against the live Twinkly cloud API.**

**Deliberate limitations:** This is *not* cloud grouping or ungrouping. It does not change LAN controllers. It refuses cloud objects that currently have a `2d` source, have multiple devices, lack required captured-PUT fields, or cannot be uniquely matched. That avoids guessing the new `geometry`, aspect and layout identifiers for a 2D default-map-to-3D camera-map conversion.

Preview (read-only except for a private backup file on the PC):

```powershell
git pull
python .\\cloud_layout_update.py --plan private-area-plan.json --member "Office Left" --object-id <OBJECT_ID>
```

**Do not execute until reviewing the private proposed PUT and confirming the intended object.** Explicitly authorised test write:

```powershell
python .\\cloud_layout_update.py --plan private-area-plan.json --member "Office Left" --object-id <OBJECT_ID> --execute --acknowledge-risk --confirm-object <OBJECT_ID>
```

The script performs no automatic retries or rollback and is not transactionally coordinated with the LAN. It writes private backups under `cloud-audits/layout-writes/` (Git-ignored). A success requires a fresh GET showing the same coordinate sequence; app UI and other account visibility must be checked independently.

For an existing `2d` default map, **do not attempt to bypass this guard**. First validate a complete 3D cloud-layout metadata object (e.g. verified original 3D cloud object) and compare with the live object's immutable and shared-installation relationships. Only then extend the updater for source change.
