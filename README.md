# Twinkly Area Group Inspector

Read-only snapshot and comparison tool for **Twinkly Area groups** assembled in the Area Editor from independently camera-mapped sets.

**Important:** This is an investigation tool, **not a confirmed backup/restore solution**. The group's Area Editor transforms (position, scale, rotation and ordering) may not be exposed by documented local API endpoints. Do **not** delete or ungroup your working display on the strength of a snapshot.

## Safety

- Only GETs are sent to device data endpoints. The sole POSTs are `/xled/v1/login` and `/xled/v1/verify`, needed for Twinkly authentication. These may invalidate another client session's API token; schedule captures when automation is idle.
- No group, mode, effect, LED or layout is ever modified.
- Explicit device IPs: no network-wide scan, no cloud account, no HA token.
- Raw device responses, including IDs, IPs, MACs and geometry, remain on your machine and are ignored by Git. Do not commit your snapshot folders.
- Unavailable endpoints are recorded as errors rather than silently treated as valid empty configurations.

## Quick start (PowerShell / Windows)

Python 3.10+; no third-party dependencies.

```powershell
git clone https://github.com/B-Hartley/twinkly-api-tools.git
cd twinkly-api-tools
python .\twinkly_inspector.py inspect --ips 192.168.1.51 192.168.1.52 192.168.1.53 --output .\snapshots
```

Include **every physical controller** in the Area group, especially its master. Use the individual device IPs from Home Assistant's Twinkly device entries or your router, not the HA entity IDs. No devices are switched on/off by this script.

Optional named configuration (copy the sample file, substitute actual local IPs):
```powershell
Copy-Item devices.example.json devices.json
python .\twinkly_inspector.py inspect --config .\devices.json --output .\snapshots
```

Compare snapshots (e.g. before and after a normal effect change, **without ungrouping**):
```powershell
python .\twinkly_inspector.py compare .\snapshots\SNAPSHOT_A .\snapshots\SNAPSHOT_B
```

`manifest.json` summarizes the run; `controllers/<ip-safe>/responses/*.json` contains full raw responses; `report.txt` highlights available coordinates, group-related clues and failures. Each snapshot is timestamped.

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
