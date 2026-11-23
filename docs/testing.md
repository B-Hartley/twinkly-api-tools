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

See API_REFERENCE.md for the observed API commands and grouping payloads.

## Firmware compatibility

The tested firmware and hardware table is maintained in [the local API reference](API_REFERENCE.md).

## Partial regroup recovery (23 Nov 2026)

After regroup was interrupted by HTTP 401 on the master group-assignment POST, do not repeat the operation. Instead run the fully **read-only** status check:

~~~powershell
git pull
python .\group_status.py --plan private-area-plan.json
~~~

The report checks both controllers' group roles and compares the live layout to the saved Area and standalone coordinates. Upload the console output and locally saved `group-test-results.json` (redact private IPs if sharing publicly). The failure could be token invalidation or competing client authentication, but its root cause is unconfirmed. Do not assume all writes completed or none completed.

For complete endpoint coverage, see the [single API reference](API_REFERENCE.md).

## Safely resuming a partially completed regroup

For the 23-Nov-2026 interrupted Office attempt, the read-only status tool reported:
- Office Left: mode `none`, mapping `area`
- Office Right: mode `none`, mapping `standalone`

`resume_regroup.py` recognises only exact saved mappings and expected group roles. It reads every member before changing any, refuses unknown states, and re-authenticates for each write. It NEVER automatically retries a POST after an HTTP 401 or other error.

First preview the currently required steps (network GET/login only, no LED/group changes):

```powershell
git pull
python .\resume_regroup.py --plan private-area-plan.json
```

Expected (if state is unchanged): Office Left needs ONLY `/application/group`; Office Right needs `/led/layout/full` followed by `/application/group`. This preview is based on **live controller reads**, so it will refuse mismatches.

Only after reviewing the preview and accepting the remaining risk, run the explicit live command:

```powershell
python .\resume_regroup.py --plan private-area-plan.json --execute `
  --acknowledge-risk --group-confirm "Office Windows"
```

The program rechecks each controller state before every step and reads back membership/coordinates after successful writes. It logs locally to `resume-regroup-results.json`. Even with fresh authentication, a 401 may recur; **do not rerun blindly**. The procedure does not restore app-side group names, effects or playlists.

## Force-restore individual standalone camera mappings (Office test, 23 Nov 2026)

Use this only when the user intends to **overwrite the current layout** with the backed-up standalone `3d` coordinates. The tool does **not** change group membership; it requires every member's group mode to be `none` before sending any writes. It reads the current layouts and hardware LED counts, saves a local before-image to `recovery-backups/`, re-authenticates before uploads, and verifies the original 3D coordinates on both controllers.

Read-only preview (makes auth POSTs but no layout/group changes):

```powershell
git pull
python .\restore_standalone.py --plan private-area-plan.json
```

Live **overwrite**, only after preview shows the expected controllers and backup is verified:

```powershell
python .\restore_standalone.py --plan private-area-plan.json --execute `
  --acknowledge-risk --group-confirm "Office Windows"
```

Afterwards verify with `python .\group_status.py --plan private-area-plan.json`. Stop if any error occurs. This operation deliberately does **not** restore the Area group or app/cloud records; leave the Twinkly app closed during recovery. Private backup and logs are ignored by Git.

## Unfiltered Office join capture (post-filtered)

To identify cloud create/remove requests reliably, record **one short Office join** without an in-capture host filter. This also avoids losing requests if the app uses a new cloud hostname. The original capture is preserved privately, and a second Twinkly-only copy is generated afterwards. **Both files can contain authentication secrets; filtering is not credential redaction.**

With the iPhone configured to use the Windows PC as its Wi-Fi HTTP proxy on port 8080, run in the repository directory:

```powershell
git pull
.\\Scripts\\Start-OfficeJoinCapture.ps1
```

Start capture first, then open Twinkly, **join the two Office windows once** and wait for completion. Press **Ctrl+C** in the PowerShell capture window to stop; the script creates two files under `C:\\TwinklyCapture`:

- `office-join-<UTC>-RAW-PRIVATE.flows`: original unfiltered source; keep locally and never commit or share.
- `office-join-<UTC>-twinkly-PRIVATE.flows`: filtered Twinkly domains and Office controller IPs; useful for private analysis but **still contains credentials**, and should not be posted publicly.

Turn off the iPhone manual proxy after capture. If the PowerShell session is interrupted before filtering finishes, run `python .\\filter_twinkly_flows.py <raw-file> <filtered-file>` on the saved file. This does not include the Windows script's own direct cloud requests unless the PC itself also uses the proxy.

If possible, don't run the LAN audit while the app is joining. The local login/verify calls can conflict with the app's controller session; run the read-only status check only after the join completes.
