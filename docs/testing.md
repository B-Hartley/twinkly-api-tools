# Twinkly API tests and experimental Area-group round trip

All test outputs are local. Do not commit device IPs, snapshots, authentication tokens or private plans.

## Read-only GET compatibility sweep

Run from the repository root in PowerShell:

~~~powershell
git pull
python .\probe_api.py
~~~

Each controller from devices.json is tried independently; unreachable ones are skipped.
Results go to api-probes/<UTC timestamp>/: api-matrix.json, report.md and per-controller JSON files.
The matrix records each controller's firmware metadata, endpoint success/error, app code and response schema.
HTTP 200 with a non-1000 Twinkly app code is not a success. A failed endpoint may be firmware-specific.
Network scanning is excluded to avoid even potentially disruptive GET behaviour.

Review for private IPs, MAC addresses and other identifiers before sharing results.

## Experimental group round-trip (disposable test group only)

Requires two inspector snapshots: grouped (with 2D Area layouts) and standalone
(with each controller's original 3D camera mapping). A grouped snapshot alone is NOT sufficient.

Build a private plan offline:

~~~powershell
python .\build_group_plan.py --grouped .\snapshots\GROUPED `
  --standalone .\snapshots\STANDALONE `
  --members "Office left" "Office right" `
  --name "Office Windows" --out private-area-plan.json
~~~

If the parser cannot recognise your firmware's summary shape, it stops; do not fabricate group metadata.
The plan intentionally has preverified_backup=false. Review identities, UUID, offsets, camera mappings
and Area coordinates, and only set it true after independent validation of the backup.

Dry-run only (NO network access; no controller changes):

~~~powershell
python .\group_test.py ungroup --plan private-area-plan.json
python .\group_test.py regroup --plan private-area-plan.json
~~~

Experimental live commands are intentionally hard to use. Only for a disposable group:

~~~powershell
python .\group_test.py ungroup --plan private-area-plan.json --execute `
  --acknowledge-risk --group-confirm "Office Windows"
python .\group_test.py regroup --plan private-area-plan.json --execute `
  --acknowledge-risk --group-confirm "Office Windows"
~~~

WARNING: Partial failures can leave a display partly grouped. The app also manages playlist/effects/timer
settings not fully reproduced by this script. Successful POSTs are not proof of visual restoration.
Never attempt the live commands on an irreplaceable Area display until a disposable test succeeds.

See captured-api.md for the observed API commands and grouping payloads.

## Firmware indexing

Probe output is the evidence source for firmware-indexed documentation. Existing documentation describes
observed traffic and remains provisional until matched to per-device firmware and replay tests.
