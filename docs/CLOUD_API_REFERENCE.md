# Twinkly cloud API — captured installation objects and layouts

> **Unofficial reverse-engineering reference.** Updated 23 November 2026. Based on the user's four original mitmproxy captures: `twinkly.flows`, `twinkly(1).flows`, `twinkly(2).flows`, and `twinkly(3).flows`. This is the **cloud** API at `https://api.twinkly.com`, which is separate from the local controller API at `http://DEVICE_IP/xled/v1/`. No live cloud writes have been made by this repository.

## Findings at a glance

The captures **do prove** that the iOS app creates and removes persistent cloud installation objects as it groups and ungroups Office Windows. The cloud representations include the device members, group name and complete encoded geometry. The local Python ungroup/regroup and standalone-layout restore scripts **do not** update cloud objects. That explains why the app **could** continue to show a default or outdated map even though direct LAN reads confirm the correct 3D coordinates; however, the traces alone do **not prove** whether an individual app screen is reading cloud state versus a local cache.

**Follow-up capture (23 November 2026):** `mapping-upload.flows` confirms that Twinkly's app updates an **existing** cloud object using `PUT /v3/objects/{objectId}`. It uploaded one 210-coordinate `3d` previous mapping and subsequently a 210-coordinate `2d` default mapping to the *same* object, both HTTP 200. The route is observed, but no third-party script has yet written to the cloud. Do not use `POST /v3/objects` for an in-place edit; captured POST creates objects.

## Captured request inventory

| Method | Path | Captured evidence | Result | Meaning |
|---|---|---|---|---|
| `GET` | `/v2/users/me` | All four traces | 200 | Read authenticated account identity |
| `GET` | `/v3/installations` | Two traces | 200 | Retrieve installations / object references |
| `GET` | `/v3/objects` | Two traces (two calls each) | 200 | Retrieve saved objects, optionally with query selectors such as `mine`, `fields` or `objectIds` |
| `PUT` | `/v3/installations/{installationId}/objects` | Twice | 200 | Remove an object reference from an installation using `action:"remove"` |
| `POST` | `/v3/objects` | Five times | 201 | Create a new single-controller or combined Area object |
| `PUT` | `/v3/objects/{objectId}` | Twice in follow-up `mapping-upload.flows` | 200 | Replace/update an existing object's data and saved mapping (same object ID) |

The captures do not establish a stable public Twinkly API contract; these routes and schemas could change without notice. The fifth, follow-up capture covers an existing-object update without creating a new object.

### Capture-by-capture event sequence

- `twinkly.flows`: cloud `GET /v2/users/me` observed; no object-write cloud requests in that file.
- `twinkly(1).flows`: removal of one installation object, creation of **two 210-LED standalone 3D objects**, then installation and object reads.
- `twinkly(2).flows`: creation of **one 420-LED 2D Area object** with two device members.
- `twinkly(3).flows`: another installation-object removal followed by **two standalone 210-LED 3D object creations**, then installation and object reads.

The request and response ordering is supported by the captured flows. It does not prove which UI tap initiated each call without a synchronised user-action timeline.

## Authentication

The captured app's cloud requests used an `Authorization: Bearer <secret>` header over HTTPS. **This is not the local Twinkly `X-Auth-Token` challenge/verify mechanism**. Tokens, account IDs, installation IDs, object IDs, MAC addresses, UUIDs, credentials and personal identifiers from the source captures must remain private. Do **not** replay an extracted mobile access token in a public script or check it into Git.

The captured object payloads contain device credential-related fields including `password`, `challenge` and `challengeResponse`. Treat raw traces and original cloud payloads as **sensitive**; this page intentionally omits all values.

## Cloud object update: `PUT /v3/objects/{objectId}` — confirmed in follow-up trace

The newly uploaded `mapping-upload.flows` contains **two** authenticated requests to `https://api.twinkly.com/v3/objects/{objectId}`, targeting the **same** pre-existing Office object ID. Both returned HTTP **200**, and both responses contained JSON bodies (their fields are not fully characterised here). This was **app traffic**, not an action performed by our scripts.

| Captured user operation | Local controller operation | Cloud object operation | Decoded geometry |
|---|---|---|---|
| Restore previous mapping | `POST /xled/v1/led/layout/full` → HTTP 200 | `PUT /v3/objects/{objectId}` → HTTP 200 | `layout.source: 3d`, 210 coordinates |
| Restore default mapping | `POST /xled/v1/led/layout/full` → HTTP 200 | `PUT /v3/objects/{objectId}` → HTTP 200 | `layout.source: 2d`, 210 coordinates |

Request body keys observed on both requests were `name`, `uuid`, `ledCount`, `devices`, `ledProfile`, `fps`, `capacity`, `meta`, `palette`, and `layout`. The **existing object ID is in the URL**; there is no `id` key in the submitted JSON body. Nested `layout` keys: `coords`, `source`, `geometry`, `uuid`, `aspectXZ`, `aspectXY`, `name`, `synthesized`. The `layout.coords` string decodes to a **210-element JSON array** with `x`, `y`, `z` coordinates in both requests, using the Base64+GZIP encoding described below. The captured encoded strings were different lengths, reflecting different geometry data, but their opaque values and IDs remain private.

These PUT requests contain **more than a layout**: they supply object and device metadata too. Do not assume that a hypothetical minimal `{ "layout": ... }` PATCH or PUT is accepted or preserves other fields. An eventually developed client must obtain and privately back up the current cloud object, preserve the correct ownership/membership/metadata and validate the intended object before any write.

**Additional observed calls around the default reset:** the trace includes `DELETE /v3/reconstruction/snapshot/{uuid}` (HTTP 404) and `DELETE /v3/effects/compiled/{uuid}` (HTTP 204). These may be app cleanup operations; the causal relationship has not been verified and they should not be included in a cloud mapping updater by assumption.

**Caution:** HTTP 200 on the cloud PUT proves the server accepted the request, not yet that the Twinkly app will persistently render an externally-written map after later refresh or effect operations. No custom cloud PUT has been attempted.

## Cloud object creation: `POST /v3/objects`

This operation was captured with HTTP **201 Created**, including a response body describing the newly created object. Representative, **illustrative only** request structure:

```json
{
  "name": "Example Office Set",
  "installationId": 1234,
  "ledProfile": "<captured profile>",
  "capacity": 210,
  "fps": 25,
  "meta": "{}",
  "uuid": "<object uuid>",
  "ledCount": 210,
  "devices": [
    {
      "part": 0,
      "device": {
        "name": "Example controller",
        "uniqueId": "<private>",
        "productCode": "TWW210SPP",
        "hwVersion": "300",
        "ledCount": 210
      }
    }
  ],
  "layout": {
    "source": "3d",
    "coords": "<base64-encoded gzipped JSON array of coordinates>",
    "geometry": "<geometry type>",
    "uuid": "<layout uuid>",
    "aspectXZ": 1.0,
    "aspectXY": 1.0,
    "name": "<layout name>",
    "synthesized": false
  }
}
```

**Not replayable:** This is a shortened/redacted *schema illustration*, not a complete or valid substitute for the app's creation request. The genuine device objects include other required/sensitive fields. Example `fps`, `capacity`, `aspect*` values are placeholders and **not asserted as observed fixed values**.

Observed request object top-level fields: `name`, `installationId`, `ledProfile`, `capacity`, `devices`, `fps`, `meta`, `layout`, `uuid`, `ledCount`. Field order varied and is not significant.

### `devices[]` and membership

`devices[]` contains records with `part` and a nested `device` description. The captured nested device keys included `name`, `fps`, `mac`, `ledProfile`, `family`, `uniqueId`, `networkMode`, `productCode`, `hwVersion`, `password`, `ip`, `capacity`, `meta`, `ledCount`, `firmware`, `prodTs`, `challenge`, and `challengeResponse`. Do not print or distribute raw values.

A standalone object has **one** device member with `ledCount=210`; the Area object has **two** device members with `ledCount=420`. For the Area creation, the request's `layout.source` was `2d`; the standalone request's `layout.source` was `3d`.

### `layout.coords` — encoding confirmed by decoding the saved captures offline

**Important difference from the local REST API:** the cloud layout stores `coords` as a **string**, not a directly embedded JSON array. The string is **base64(gzip(UTF-8 JSON array))**. Decode in this order:

```python
import base64, gzip, json
coordinates = json.loads(gzip.decompress(base64.b64decode(layout["coords"])))
```

For the captured single-controller object, the decoded array had **210** objects; the captured combined Area object had **420** objects. Each decoded coordinate is a JSON object with `x`, `y`, `z` numeric fields. The encoded field is **not** a UUID or a list of device identifiers. The compressed encoding must be preserved correctly in any future cloud write. The cloud `source` label describes the geometry type even though `x`, `y`, `z` keys may all be present.

When preparing a potential cloud-to-LAN comparison, independently validate the decoded coordinate count, device membership, coordinate order and numeric values. **Do not assume** that a cloud object with the same device names necessarily uses the latest layout from those physical controllers.

### Successful creation response: observed schema

The JSON returned on HTTP 201 includes an assigned object `id`, `name`, `ledCount`, `ledProfile`, `fps`, `capacity`, `opMode`, `ownerId`, `layout`, `users[]`, `devices[]`, `uuid`, `caps[]`, `meta`, `installation`, `palette[]`, `created`, and `updated`.

The nested `layout` includes `id`, `objectId`, `name`, `geometry`, `aspectXY`, `aspectXZ`, `coords`, `source`, `synthesized`, `imageUrl`, `uuid`, `created` and `updated`. These IDs and timestamps are cloud-side metadata, distinct from local controller group membership.

## Installation object removal: `PUT /v3/installations/{installationId}/objects`

Observed **request shape**:

```json
{"objectIds": [123456], "action": "remove"}
```

Here the ID is **fictitious**. The two captured calls both returned HTTP 200. **This is removal of an installation object reference**, not a tested `DELETE` request and not evidence of full object deletion or automatic cleanup. The 200 responses' bodies were not fully decoded in this analysis, so their detailed schema is not asserted here.

## Cloud reads: `GET /v3/objects`, `GET /v3/installations`

The mobile app successfully called these GET routes (HTTP 200). It used object-list selection parameters, including observed `mine`, `fields` and `objectIds` query values in the research notes. Response bodies were not fully characterised in this page; do not infer field completeness from the creation response. The `GET /v2/users/me` request identifies the signed-in user but does not itself change grouping.

## Relationship to local Twinkly controller REST API

| Responsibility | Local `/xled/v1/` | Cloud `api.twinkly.com` |
|---|---|---|
| Membership state | `GET/POST application/group`; `none/master/slave`, shared UID, size, offsets | Object membership `devices[]`, installation and object IDs |
| Geometry | `GET/POST led/layout/full`; raw `coordinates[]` array | Encoded `layout.coords` plus metadata |
| Friendly group name | Not present in observed group endpoint | `object.name` captured |
| Authentication | Challenge, verify, `X-Auth-Token` | App bearer authorization |
| Verified Python operations | Local group round trip and standalone 3D map restoration on Office devices (FW 2.11.0) | **No Python cloud writes verified or attempted** |

The restored standalone coordinates on both Office controllers were independently verified by local GET. That **does not** update the captured cloud objects.

## A cautious path to fixing an out-of-date app map

1. **Read-only cloud inventory:** obtain a *fresh*, user-authorised `GET /v3/objects` response (using the signed-in app or a permitted account session). Do not reuse stale access tokens from historical traces.
2. Identify **existing** Office Left/Office Right objects by a combination of installation and device identity, not merely name.
3. Save the complete original cloud object responses **privately** and compare the decoded `layout.coords` against the verified 3D layouts in the private local plan.
4. We now have the captured **existing-object update** method: `PUT /v3/objects/{objectId}` in `mapping-upload.flows`. Before automating it, verify a fresh signed-in cloud GET, the intended object ID, complete required body fields and successful readback. **`POST /v3/objects` is a create, not an update**.
5. If attempting an independent PUT test later, test only on the disposable Office object with explicit confirmation, preserve identity/ownership, and immediately GET the object again to verify cloud persistence. Stop if object IDs, membership or mappings differ unexpectedly.
6. Refresh/restart the iOS app to verify visual geometry. Independently recheck local controller state; the app might also push a layout back to a controller.

**No cloud update has been executed by these Python tools.** Two cloud PUT updates were observed from the official Twinkly app. The source traces remain private. This file contains only anonymised structural observations.

See also [local controller API reference](API_REFERENCE.md).
