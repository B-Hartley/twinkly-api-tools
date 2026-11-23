# Twinkly Cloud API Reference

> **Unofficial, reverse-engineered API.** This page documents protocol behaviour, not individual captures or installations. **Confirmed** means observed in successful app requests/responses; **inferred** means a reasonable interpretation not independently proved; **untested** means no compatible external client write has been verified. The endpoints and structures may change.

The cloud service at `https://api.twinkly.com` stores account, installation and object records. It is **separate** from the LAN controller API at `http://DEVICE_IP/xled/v1/`. See [Local API Reference](API_REFERENCE.md).

## Authentication and response handling

- **Confirmed:** Cloud requests use HTTPS with `Authorization: Bearer <access-token>`. This is **not** the LAN `X-Auth-Token` mechanism.
- **Confirmed:** Successful cloud responses use conventional HTTP status codes such as **200 OK**, **201 Created** and **204 No Content**. Unlike the local controller API, a Twinkly JSON `code:1000` field is not required for cloud calls.
- **Untested:** How an independent client should obtain/refresh a cloud token. Do not hard-code, commit or replay captured app tokens. Cloud payloads may contain account, installation and device credential fields; keep complete responses private.

## Endpoint index

| Method | Relative path | Purpose | Evidence |
|---|---|---|---|
| GET | `/v2/users/me` | Get authenticated account information | **Confirmed** 200 |
| GET | `/v3/installations` | Get installations and their object references | **Confirmed** 200 |
| GET | `/v3/objects` | Get saved light objects; supports query selectors | **Confirmed** 200 |
| POST | `/v3/objects` | Create a saved light object | **Confirmed** 201 |
| PUT | `/v3/objects/{objectId}` | Update an *existing* object, including layout | **Confirmed** 200 |
| PUT | `/v3/installations/{installationId}/objects` | Modify installation-object association | **Confirmed** 200 for `action:"remove"` |
| POST | `/v3/reconstruction` | Submit calibration/reconstruction input for processing | **Confirmed** 201 |
| GET | `/v3/reconstruction/{id}` | Obtain reconstruction job result and status | **Confirmed** 200 |
| GET | `/v3/reconstruction/snapshot/{uuid}` | Look up saved reconstruction snapshot | **Endpoint observed**, response can be 404 |
| DELETE | `/v3/reconstruction/snapshot/{uuid}` | Snapshot cleanup | **Endpoint observed**, success not established |
| DELETE | `/v3/effects/compiled/{uuid}` | Compiled effect cleanup | **Confirmed** 204; mapping dependency **unproven** |

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

**Confirmed:** requests may use selectors such as `mine`, `fields`, `objectIds` (availability and supported combinations not exhaustively characterised). `GET /v3/installations` retrieves installation metadata. **Inferred:** object records are an important source for the app's inventory and displayed maps; app-specific caching and refresh behaviour has not been proven.

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

**Untested:** Minimal layout-only PUT, optimistic concurrency, response semantics after external-client writes, and what app refresh does after such a write. An updater should first read and privately back up the complete current object, preserve immutable/ownership fields and membership, verify the target ID, then perform a separately authorised test. This repository does not yet implement authenticated cloud writes.

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
- **Untested:** automated cloud login/token refresh, independent programmatic object PUT, app refresh semantics, deletion/rollback behaviour, and reconstruction parameters for arbitrary devices.
- **Safety:** compare target object identity and decoded geometry before any write; never publish raw captures, object credentials, access tokens or identifying values.

The cloud API is **not documented or guaranteed by Twinkly**. Use it at your own risk.
