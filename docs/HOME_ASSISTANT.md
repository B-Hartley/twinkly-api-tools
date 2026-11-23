# Home Assistant / HACS (experimental)

HACS can install this repository as a **custom integration**, after adding `B-Hartley/twinkly-api-tools` as a custom integration repository. After installation restart Home Assistant and go to **Settings → Devices & services → Add integration → Twinkly Area State Inspector**.

The configuration form expects a **JSON array**, e.g.:

```json
[
  {"name":"Office Left","ip":"192.168.21.10"},
  {"name":"Office Right","ip":"192.168.21.11"},
  {"name":"BackR Bush Bottom","ip":"192.168.21.12"}
]
```

Use your actual device IPs. The default config doesn't automatically read `devices.json` in the Git repo: enter devices in the HA configuration form. A single integration instance manages all the named controllers.

## Actions (Developer Tools → Actions)

Capture existing Front state (run while Front is still grouped):

```yaml
action: twinkly_area_state.save_state
data:
  name: frontgroup
```

Capture an ungrouped state (**only if you voluntarily ungroup**, since automatic restoration is not available):

```yaml
action: twinkly_area_state.save_state
data:
  name: ungrouped
```

Audit whether the saved state matches current controllers; ***this does NOT restore the group***:

```yaml
action: twinkly_area_state.restore_state
data:
  name: frontgroup
```

For a disposable office-window geometry proposal, keep both office windows independent and camera-mapped first. Capture:

```yaml
action: twinkly_area_state.save_state
data:
  name: office_individual
```

Then:

```yaml
action: twinkly_area_state.office_window_plan
data:
  name: office_individual
  left: Office Left
  right: Office Right
```

The plan scales both mappings to an equal 1x1 footprint and places them horizontally with one full window-width empty space between them, at X=0..1 and X=2..3 respectively. It preserves each source LED's original Z in the in-memory calculation (does not claim physical 3D accuracy). **It does not create an actual Area group or save a restorable group.**

Results are reported as **persistent notifications** in Home Assistant. Saved snapshots are stored in HA's local `.storage` under `twinkly_area_state.states` and should remain in your normal HA backups. Snapshots are not sent to GitHub, cloud, or Twinkly; capture requires local Twinkly login/verify POSTs, which may invalidate another client session.

## Safety

- This is **experimental**, not published in the HACS default catalog.
- Restore action is intentionally read-only. It checks mapping and identity but never issues group creation or layout updates.
- **Do not delete any existing mapped Area group based on this integration.** We still need a proven reversible round-trip against a disposable group.
- Offline controllers are skipped and named in capture notification. Missing layouts are reported.
- Named captures overwrite that **same name** (create a new name to keep prior captures).
- HA snapshot data contains device identifiers and physical mapping coordinates. Keep HA backups private.
- Groups, LED maps, colour effects, and Home Assistant light control are not changed.
- Future regroup support must use observed and validated protocol writes and require explicit opt-in after testing.

## Current limitations

The experimental integration saves raw data and offers readiness checks; it cannot yet automatically switch between group/ungroup states. Also, the office proposal is a calculation rather than a Twinkly API operation. A real, safe switcher requires testing the create-group protocol on **Office Left** and **Office Right** first.
