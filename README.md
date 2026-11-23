# Twinkly Area Group Inspector + Home Assistant integration

Read-only snapshot and comparison tool for **Twinkly Area groups** assembled in the Area Editor from independently camera-mapped sets.

**Important:** This is an investigation tool, **not a confirmed backup/restore solution**. The group's Area Editor transforms (position, scale, rotation and ordering) may not be exposed by documented local API endpoints. Do **not** delete or ungroup your working display on the strength of a snapshot.

## HACS Home Assistant integration (experimental)

This repo now contains `custom_components/twinkly_area_state`, installable as a **HACS custom repository**. It supports named controller snapshots via `twinkly_area_state.save_state`, a **read-only** `restore_state` audit and an `office_window_plan` for two equally sized windows with a gap of one window width.

**The Home Assistant integration's restore service cannot yet actually regroup or upload saved mappings.** The restore service deliberately performs a dry run while the Twinkly Area-group create/remove API remains unverified. Use **Office Left** and **Office Right** as the disposable test group before any attempt to modify your Front display.

See [Home Assistant setup and actions](docs/HOME_ASSISTANT.md).

## Safety

- Only GETs are sent to device data endpoints. The sole POSTs are `/xled/v1/login` and `/xled/v1/verify`, needed for Twinkly authentication. These may invalidate another client session's API token; schedule captures when automation is idle.
- No group, mode, effect, LED or layout is ever modified.
- Explicit device IPs: no network-wide scan, no cloud account, no HA token.
- Raw device responses, including IDs, IPs, MACs and geometry, remain on your machine and are ignored by Git. Do not commit your snapshot folders.
- Unavailable endpoints are recorded as errors rather than silently treated as valid empty configurations.
- Unreachable controllers are skipped after a single initial connection probe. Reachable controllers with authentication or layout problems remain in the report as warnings.

## Quick start (PowerShell / Windows)

Python 3.10+; no third-party dependencies.

```powershell
git clone https://github.com/B-Hartley/twinkly-api-tools.git
cd twinkly-api-tools
Copy-Item devices.example.json devices.json
# Edit devices.json with your actual controller IPs, then run:
python .\twinkly_inspector.py inspect
```

Include **every physical controller** in the Area group, especially its master. Use the individual device IPs from Home Assistant's Twinkly device entries or your router, not the HA entity IDs. No devices are switched on/off by this script.

The inspector automatically reads `devices.json` from the current working directory. Unreachable sets are skipped; the final console summary and `report.txt` list skipped sets and reasons. If every controller fails, the command exits non-zero. For a different config file:
```powershell
python .\twinkly_inspector.py inspect --config .\my-other-devices.json
```

Compare snapshots (e.g. before and after a normal effect change, **without ungrouping**):
```powershell
python .\twinkly_inspector.py compare .\snapshots\SNAPSHOT_A .\snapshots\SNAPSHOT_B
```

`manifest.json` summarizes the run; `controllers/<ip-safe>/responses/*.json` contains full raw responses; `report.txt` highlights available coordinates, group-related clues and failures. Each snapshot is timestamped.

## Capture and compare named group states

**Before deleting your Front group**, take a fresh named snapshot and preserve your Area Editor screenshot:

```powershell
python .\\twinkly_inspector.py inspect --group-name frontgroup
```

The snapshot is saved under `snapshots/frontgroup/<UTC timestamp>/`. If **any of the five Front controllers fails to capture its layout, stop**. Keep a separate copy of the snapshot directory before experimenting. Existing snapshots are never overwritten.

After **you choose** to delete/ungroup the Area group using the Twinkly app, capture the new state:

```powershell
python .\\twinkly_inspector.py inspect --group-name ungrouped
```

Compare the two latest named snapshots and produce a restoration readiness report:

```powershell
python .\\state_manager.py compare --from-state frontgroup --to-state ungrouped --group-id BCA8AAE9 --output .\\restore-plan.json
python .\\state_manager.py restore --from-state frontgroup --to-state ungrouped --group-id BCA8AAE9
```

**The `restore` command is deliberately a dry-run plan only. It will NOT regroup or upload layouts.** Twinkly does not publish a supported group-creation API, and the reverse-engineered commands are not verified. If you delete the current group, recreating its precise Area Editor arrangement may still require manual work. Run the two captures now only if you accept that risk; preferably test with a spare disposable group first. Keep any offline or missing device reports.

`--group-id` is essential if your captured controllers report more than one group (your earlier snapshot did). The plan checks membership, original master, controller IDs, LED counts, reachable devices and coordinate snapshot hashes. If the ungrouped state still shows any original members in groups, it flags that instead of treating them as independent.

## Visualise the Area layout

Install the one optional plotting dependency:

```powershell
python -m pip install matplotlib
```

Plot a saved snapshot (no Twinkly devices are contacted):

```powershell
python .\\visualize.py .\\snapshots\\YOUR_TIMESTAMP
```

To plot **only the Front Area group** using its master/slave group UUID prefix:

```powershell
python .\\visualize.py .\\snapshots\\YOUR_TIMESTAMP --group BCA8AAE9
```

Or select explicit named controllers as recorded in the snapshot:

```powershell
python .\\visualize.py .\\snapshots\\YOUR_TIMESTAMP --names "BackR Bush Bottom" "BackR Bush Top" "Front Left Bottom" "Front Left Top" "Front Right"
```

The command writes `layout.png` inside the snapshot directory by default. Use `--output my-layout.png` to choose another path. Each string has a different colour and label, plotted at the original API X/Y coordinates with an equal-scale aspect ratio. **This is a visual investigation, not a guaranteed restore backup.**

## Interpreting results

1. Inspect `report.txt`. Confirm all expected controllers responded, the individual LED coordinate counts appear plausible, and group-related responses were captured.
2. Retake the snapshot while the Area group is unchanged. Stable layout hashes indicate repeatable retrieval (not proof of restorable Area Editor geometry).
3. Save a screenshot of the Twinkly Area Editor showing the relative placement of each mapped set. This manual evidence is important because the REST API's complete representation is unverified.
4. Do **not** ungroup or run any layout POST/DELETE calls. A verified round-trip of Area Editor composition must be developed against a disposable test group first.

## Home Assistant

Run from a machine that can reach the Twinkly controllers (e.g. a Windows PC on your LAN, or an HA shell with Python). Home Assistant exposes controllers and IPs but does not itself provide a reliable Area Editor group export. This tool deliberately does not need or manipulate Home Assistant.

## Protocol references

- Community API: https://xled-docs.readthedocs.io/en/latest/rest_api.html
- Twinkly Area Editor instructions: https://help.twinkly.com/hc/en-gb/articles/18301582424861-step-by-step-procedure-to-create-an-area-group
- Firmware variations exist. A `404` for a read endpoint means it may not exist on that controller.

## Future work

Improve cloud-object inventory comparison and safe reconstruction/app refresh testing. The controller regroup and individual mapping restoration workflows are experimental and require verified private backups and explicit confirmation.

## Additional tools (experimental)

- `python probe_api.py` — read-only firmware-indexed GET endpoint sweep over every controller in local `devices.json`.
- `python build_group_plan.py --help` — build a private plan from grouped and standalone snapshot directories.
- `python group_test.py ungroup --plan private-area-plan.json` — dry-run by default, **no network calls**; see safety gates before live writes.
- See the **[local API reference](docs/API_REFERENCE.md)**, **[cloud API reference](docs/CLOUD_API_REFERENCE.md)**, and [test instructions](docs/testing.md).
- `python group_status.py --plan private-area-plan.json` — **read-only** membership and layout check, useful after failed/partial writes.

**Experimental:** local controller grouping and layout recovery are supported only with private verified backups, state preflight, and explicit write confirmations. App/cloud object consistency and effect/playlist restoration are separate, unverified workflows.

For a controlled **overwrite** of the current Office layout with the backed-up, individual camera-mapped 3D layouts (while *ungrouped*), see [standalone layout recovery](docs/testing.md#force-restore-individual-standalone-camera-mappings-office-test-23-nov-2026). Run the read-only preview before permitting live writes.

## Cloud inventory auditing (experimental; read-only)

`cloud_audit.py` can read the signed-in account's object and installation inventory using interactive `--login` or a bearer token supplied **locally** in `TWINKLY_CLOUD_TOKEN`. No cloud write methods are implemented; the raw responses are saved privately under ignored `cloud-audits/`. To understand how the app logs in **without disclosing credentials**, use `redact_login_trace.py` against a local `.flows` file; it emits method/path/status and top-level JSON key names only. See [cloud API usage](docs/CLOUD_API_REFERENCE.md#read-only-cloud-inventory-and-login-research).

For interactive cloud login and read-only inventory: `python .\\cloud_audit.py --plan private-area-plan.json --login`. The password is prompted without echo and a DPAPI-encrypted access/refresh session is saved under your Windows user profile. Afterwards omit `--login` to reuse the access token while valid; run `--login` again after expiry until Twinkly's refresh endpoint has been verified. The password is never saved. Run `--forget-login` to delete the cached session. Full inventory backups are ignored by Git. The new DPAPI cache has **not yet been live-tested**.

### Reuse encrypted Twinkly cloud login (Windows)

```powershell
# First time; prompt once and save DPAPI-encrypted tokens:
python .\cloud_audit.py --plan private-area-plan.json --login
# Later (while access token is still valid):
python .\cloud_audit.py --plan private-area-plan.json
# Delete encrypted token cache:
python .\cloud_audit.py --plan private-area-plan.json --forget-login
```

Token cache: `%LOCALAPPDATA%\TwinklyApiTools\cloud-session.dpapi`, encrypted for the current Windows user via DPAPI. **The refresh-token exchange has not yet been identified**. After the access token expires you must log in again; we do not store your password or attempt an invented refresh call. Do not use plaintext username/password JSON files.

### Guarded single-object cloud 3D layout preview

`cloud_layout_update.py` previews a private cloud backup and a proposed existing-object PUT. It can execute a single explicitly confirmed 3D→3D coordinate replacement, but cannot yet safely turn a 2D default mapping into 3D or synchronize cloud grouping/ungrouping. See [Cloud API Reference](docs/CLOUD_API_REFERENCE.md#experimental-cloud-layout-update-tool). **No automatic cloud writes** are made during previews.

## Offline cloud/local grouping preflight (read-only)

Use `cloud_group_preflight.py` to validate a saved stage of a cloud and local
grouping snapshot. It matches controller MACs against nested cloud device
identities, checks member order/part index and local master/slave roles, then
compares **every X/Y/Z coordinate** (tolerance 0.0002). Any mismatch or
ambiguous/partial membership fails closed. It does **not** connect to Twinkly
or enable grouping writes.

```powershell
python .\cloud_group_preflight.py `
  --archive "C:\TwinklyCapture\20261123T180540Z.zip" `
  --stage "office-join-cycle/20261123T175450Z/02-joined-attempt-01"
```

Use `--snapshot-dir PATH` instead of `--archive/--stage` for extracted
files. This is a snapshot checker, **not** a live-state guarantee: take new
snapshots immediately before any future changes. Offline regression tests:
`python -m unittest discover -s tests -p "test_cloud_group_preflight.py"`.

### Live cloud/local preflight

The recommended command reads the **current** cloud and LAN state and does not
require exporting or zipping snapshots:

```powershell
git pull
python .\cloud_group_preflight.py --group "Office Windows"
```

`--group` accepts either the saved plan's `group_name` or the current matching
cloud object name (which may have been renamed). Device selection is based on
the two controller IPs in ignored `private-area-plan.json` followed by MAC
identity verification, **not** the group name. For example,
`--group "Office resiE group"` also works when that is the current object
name and its membership matches. If the encrypted cloud session has expired,
append `--login`.

Full raw cloud and local responses and a report are privately saved under
ignored `cloud-preflight-backups/`. Never commit or publicly share these
backups: Twinkly device objects may contain credentials. Offline snapshot
modes remain available for testing.

The preflight performs no grouping or layout writes. `BLOCK` (exit code 2)
is expected when the cloud and local maps disagree, including a single LED;
`PASS_READ_ONLY` (exit code 0) checks consistency, **not permission** to
execute any future write.

### Repair a missing master role (one-controller guarded operation)

When cloud membership and all coordinates agree but the first controller
reports `none` and the second is already `slave`, use the dedicated
`repair_missing_master.py` dry run:

```powershell
git pull
python .\repair_missing_master.py --group "Office Group"
```

This is **not** a generic grouping command. It is strictly scoped to the
observed missing-master state: one 2-member cloud object, matching MACs and
coordinates, correctly configured slave and standalone master. It reuses
the **live slave's group UUID**, not a stale plan value, and only constructs
a master-role assignment. Before any enabled write, it performs two fresh
cloud + LAN audits with private backups, checks for state drift, and
authenticates the target controller. The execution path requires
`--execute --acknowledge-risk --group-confirm "Office Group" --master-confirm "Office Left"`
(the last flag must match the **first member's exact name in your private
plan**). No retries after ambiguous writes. It never changes cloud objects,
the right controller or either layout, and performs a full independent
readback after the single local POST.

**Run the dry run and inspect the result before considering live execution.**
Offline tests can be run with
`python -m unittest discover -s tests -p "test_repair_missing_master.py"`.

## Experimental CLOUD + LAN Check / Ungroup / Check / Group / Check

`cloud_group_cycle.py` is the **cloud-writing** workflow, distinct from
`group_roundtrip.py` (which only waits for app-driven changes). The new
script reads the cloud account through the cached Windows login and reaches
both physical controllers using the private plan. It creates and removes
Twinkly cloud objects using the independently **observed but not yet
client-verified** API calls, and performs LAN grouping/layout writes.
It never silently retries a cloud create or removes a different object.

**Run the live read-only readiness check first:**

```powershell
git pull
python -m unittest discover -s tests -p "test_cloud_group_cycle.py" -v
python .\cloud_group_cycle.py --group "Office Group"
```

The readiness check requires current cloud/LAN grouping to pass and locates
earlier **full standalone 3D cloud object templates** under locally ignored
`cloud-audits/` and `cloud-preflight-backups/`. It verifies the two
controller MACs, source `3d`, LED counts, and 3D coordinate matches
against the original private plan. **If templates cannot be verified, the
script stops before any writes.** You must not invent aspect/geometry metadata.

Once the readiness check succeeds and its backup requirements have been
reviewed, opt in explicitly:

```powershell
python .\cloud_group_cycle.py --group "Office Group" `
  --execute --acknowledge-risk --confirm-group "Office Group"
```

It performs a fresh full preflight and private backups, then pauses for
`UNGROUP` before any writes. It sends local `none` + 3D layout to
each controller, detaches the current cloud Area, creates two standalone
cloud objects, and verifies with live preflight. It **pauses for you to
inspect the app**; typing `GROUP` continues. It uploads the original Area
2D maps and master/slave roles, then creates a new Area cloud object,
verifies current cloud/LAN grouping and all original coordinates,
and **pauses again for your in-app playback check** (`DONE`).

This process is **experimental, potentially destructive, and not an atomic
transaction**. Group/cloud object IDs will change. On any failed or
ambiguous response, it halts without retries or rollback; local and cloud
state may then differ. Complete private snapshots and a stage log are
saved under ignored `cloud-preflight-backups/cloud-cycle-*/`.
Never publish them: they can contain Twinkly device authentication
material. The group cycle has not yet been live-tested independently.

## Immutable 3D camera-map references (Office and selected lights)

`archive_3d_maps.py` stores a private **reference-3d.json per physical MAC
address** under gitignored `3d-map-archive/by-mac/`. References include ordered
X/Y/Z coordinates, source, synthesized flag, LED count, provenance, timestamp
and SHA-256 checksum. Existing references are never silently overwritten. They
are not uploaded to the cloud or changed on controllers.

With Office **currently ungrouped and 3D mapped**, archive it directly:

```powershell
git pull
python -m unittest discover -s tests -p "test_archive_3d_maps.py" -v
python .\archive_3d_maps.py --plan private-area-plan.json
```

Archive the other requested sets using the private `devices.json` names:

```powershell
python .\archive_3d_maps.py --devices devices.json `
  --names "Light Tree" "Mid Right" "BackL Bush" "Icicles"
```

The name list is case-insensitive and deduplicated. A named device which is
currently not mapped in 3D is **skipped**, not relabelled as a 3D reference.
Missing names from `devices.json` cause an error. Archive only captures
the current mapping; if it is not the desired original 3D camera map, restore
it separately before archiving.

The archive is PRIVATE (MACs and exact geometry), ignored by Git. Back it up
securely outside the repository. To verify the Office maps against the plan
and live hardware identity later:

```powershell
python .\archive_3d_maps.py --verify-plan private-area-plan.json
```

### Cloud cycle starting from currently UNGROUPED Office controllers

The group-cycle command now supports the reverse order:

**CHECK standalone → GROUP → CHECK grouped → UNGROUP → CHECK standalone**.

The first read-only check captures/verifies immutable Office 3D references,
identifies the two standalone cloud objects by MAC and searches existing
private cloud backups for verified **2D Area metadata**. No cloud/LAN writes:

```powershell
python -m unittest discover -s tests -p "test_cloud_group_cycle_reverse.py" -v
python .\cloud_group_cycle.py --start-ungrouped --group "Office Group"
```

Only after it prints **READY FOR REVERSE CYCLE**, an explicitly confirmed
execution can be attempted:

```powershell
python .\cloud_group_cycle.py --start-ungrouped --group "Office Group" `
  --execute --acknowledge-risk --confirm-group "Office Group"
```

The script pauses before GROUP (type `GROUP`), after grouping/checking to
let you inspect the Twinkly app (type `UNGROUP`), and after restoring the
independent 3D setups/checking (type `DONE`). Each stage reads fresh cloud
and local state and saves private backups. It uses the chosen cloud name
`Office Group`, even if an older Area template had a different name.

**Experimental cloud writes:** Cloud object IDs change and operations are not
atomic. On any failed or uncertain response the script stops without retry
or automatic rollback. Do not execute unless the readiness test succeeds and
the backup/metadata checks are satisfactory.

## Permanent verified 2D grouped Area archives

The `2d_group_archive/` directory is a private, gitignored, **immutable
reference store**, separate from `3d-map-archive/` for original standalone
3D geometry. Each entry is keyed by the two device MACs (ordered Left/Right)
and the cloud/local group UUID, and includes the entire cloud Area object,
both local 2D maps and both local group role records. Each saved component has
a SHA-256 checksum. Existing entries are validated but never overwritten.

Create a verified Area reference from the already-captured successful
**Office Group** preflight, even if the controllers have since been ungrouped:

```powershell
git pull
python -m unittest discover -s tests -p "test_archive_2d_group.py" -v
python .\archive_2d_group.py --snapshot-dir ".\cloud-preflight-backups\20261123T191610558142Z"
```

No cloud/LAN access or writes occur. The archive command requires that saved
cloud and both local snapshots jointly pass the full read-only grouped
preflight, including identities, roles, ordered coordinates, and matching
cloud/local group UUIDs. A later `cloud_group_cycle.py --start-ungrouped`
readiness check will prefer this checksum-verified archive over historic
snapshot discovery and will use its exact 2D local layouts in the script.
The private `3d-map-archive/` remains authoritative for original standalone
3D maps. Back up both private archive directories outside the repository.

## Read-only Twinkly movie/effects inspection

The inspector now captures the documented Twinkly **GET** endpoints for
`/xled/v1/movies` (all movie metadata), `/xled/v1/playlist` (playlist
entries), `/xled/v1/led/effects` (built-in effect catalogue), and
`/xled/v1/led/movies/current` (currently selected movie). It also keeps
the existing mode, movie configuration, selected built-in effect, layout,
and controller information.

To capture the current grouped Office lights (no device/cloud writes):

```powershell
python .\twinkly_inspector.py inspect --ips 192.168.21.90 192.168.21.93 --group-name office-with-effects --output snapshots
```

Copy the **exact** `Saved snapshot:` directory from the output into the
metadata-archive command, e.g.:

```powershell
python .\archive_effects.py --snapshot-dir ".\snapshots\office-with-effects\<TIMESTAMP>"
python -m unittest discover -s tests -p "test_effects_capture.py" -v
```

`effects-archive/` is private, gitignored, content-addressed, and
create-only. It stores the movie catalogue, playlist, selected playback
state, and effect metadata for each controller; a checksum detects
tampering on repeated captures.

**Not yet a fully restorable custom effect backup:** the public
reverse-engineered Twinkly documentation describes movie frame **uploads**
but does not identify a supported read-only endpoint for downloading
already-stored raw frames. No undocumented or destructive movie API
probes are attempted. Keep the group and its effects in the app
until raw frame capture or a reliable recreation method is validated.

## Read-only MQTT diagnostics

The Twinkly inspector also fetches the authenticated
`GET /xled/v1/mqtt/config` endpoint. Its private snapshot contains the
configured broker, port, client ID, username and keepalive when available;
any `password` or secrets are redacted before saving. The metadata
archive includes the scrubbed MQTT configuration. This endpoint is a
read-only local API request: it does **not** connect to the MQTT broker.

```powershell
git pull
python -m unittest discover -s tests -p "test_mqtt_probe.py" -v
python .\twinkly_inspector.py inspect --ips 192.168.21.90 192.168.21.93 --group-name office-mqtt --output snapshots
```

To test broker connectivity and MQTT CONNECT authentication from your PC:

```powershell
# Check certificate-verified TLS and whether anonymous MQTT CONNECT works:
python .\probe_mqtt_broker.py --port 8883

# If the firmware's local config identifies user=twinkly32,
# test a username-only CONNECT (no password transmitted):
python .\probe_mqtt_broker.py --port 8883 --username twinkly32

# Older devices may use plain MQTT on port 1883:
python .\probe_mqtt_broker.py --port 1883
```

The broker probe is limited to `mqtt.twinkly.com` on ports 1883 and
8883. It uses a random client ID so it cannot displace a light's active
connection. It sends only MQTT CONNECT and (on acceptance) DISCONNECT:
**never SUBSCRIBE, PUBLISH or commands**. An accepted CONNECT does not
prove that subscribing to your own device topics is authorised or
permitted; a refused CONNECT cannot by itself establish whether a
password, an account-level token, broker-side ACL, or firmware-specific
mechanism is required.

The reverse-engineered API documents default usernames
`twinkly_noauth` (D firmware) and `twinkly32` (F/G firmware), but does
**not** establish current publicly usable broker credentials. The probe
does not use or request passwords. Do not paste private passwords
from controller config into public issues or chat.
