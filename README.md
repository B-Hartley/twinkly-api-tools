# Twinkly Area Group Inspector

Read-only snapshot and comparison tool for **Twinkly Area groups** assembled in the Area Editor from independently camera-mapped sets.

**Important:** This is an investigation tool, **not a confirmed backup/restore solution**. The group's Area Editor transforms (position, scale, rotation and ordering) may not be exposed by documented local API endpoints. Do **not** delete or ungroup your working display on the strength of a snapshot.

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

Once real snapshots have been reviewed, identify master/slave group metadata and discover whether Area Editor transformations are remotely retrievable. Restore/ungroup controls must remain **out of scope** until a complete test-group restore is demonstrably reliable.
