# Twinkly Area Manager for Home Assistant (experimental)

Home Assistant custom integration in `custom_components/twinkly_area_state/`.
Works **alongside** the built-in [Twinkly integration](https://www.home-assistant.io/integrations/twinkly/).
It does **not** replace, patch or import the native Twinkly light integration.

## Install through Home Assistant Marketplace / HACS

1. In Home Assistant, open **Settings → Marketplace** (or HACS, depending on your installation).
2. Open **Custom repositories** and add `https://github.com/B-Hartley/twinkly-api-tools` as an **Integration**.
3. Install **Twinkly Area Manager** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Twinkly Area Manager**.
5. If Home Assistant already has Twinkly controllers configured, a **multi-select list** of their names and IPs appears. Select the physical controllers to include, or choose **Enter IP addresses manually instead**.
6. If no known Twinkly controllers are available, or you select manual setup, enter a JSON list, for example:

```json
[
  {"name":"Office Left","ip":"192.168.21.90"},
  {"name":"Office Right","ip":"192.168.21.93"}
]
```

Only private IPv4 addresses are accepted. Assign static DHCP leases. The integration does not start a second LAN discovery scanner; it reuses existing Home Assistant device entries where possible.

**This is a custom-repository install**, not a claim of listing in the public Marketplace catalogue. All installed Python files live inside `custom_components/twinkly_area_state/`. Home Assistant runs it on HAOS/Linux; Windows-only DPAPI CLI credentials are **not** imported.

## Home Assistant UI / actions

A **Twinkly Area Manager** device contains three buttons, also available as actions in
Developer Tools → Actions:

| Button | Action | What it does |
| --- | --- | --- |
| Capture groups | `twinkly_area_state.capture_groups` | Save discovered local master/slave grouped 2D coordinates and installed movie/playlist metadata |
| Capture 3D maps | `twinkly_area_state.capture_3d_maps` | Save original 3D maps only when controllers are truly standalone and currently using 3D |
| Verify maps and groups | `twinkly_area_state.verify_archives` | Check the applicable saved layouts against current local controller data |

They operate through the Twinkly **local REST API only**, without modifying light, grouping, cloud or movie settings. The local login/verify handshake uses POST as required by the Twinkly API. Captured mappings and metadata are stored privately in HA's `.storage/twinkly_area_state.states` and belong in your HA backup.

The previously available `save_state`, `restore_state` (read-only comparison), and `office_window_plan` (non-destructive proposal) actions remain.

The HA capture **does not validate Twinkly cloud Area membership**. Use the companion CLI to verify cloud and local geometry together before changing groups.

## Layout response diagnostics

If **Capture groups** or **Capture 3D maps** fails with malformed JSON, press
**Capture layout diagnostics** on the Twinkly Area Manager device.

This saves **exact raw response bytes** for `GET /xled/v1/led/layout/full` and
a companion metadata file per configured physical controller. Files are written
to `/config/twinkly_area_diagnostics/`, accessible through Home Assistant's
File Editor, Samba share, or SSH. Each is uniquely timestamped, at most 1 MiB,
and saved with private file permissions where supported. Responses from login,
verify and other endpoints are never dumped.

The raw layout file may contain **private LED positions** and hardware-related
data. Review it before sharing. The saved metadata lists HTTP status, body size,
content type, and parsing error; it does not contain tokens or request headers.
The action never changes any light settings.

## Branding

A scalable `brand.svg` icon is included in the custom integration directory.
Home Assistant's core integration-icon registry is managed separately by the
Home Assistant Brands project; placing an SVG in a HACS integration does not
automatically replace the generic icon in the Devices & Services integration
catalogue. A formal brand contribution is needed for automatic display there.

## Group and Ungroup (companion Windows CLI)

**Not yet exposed as Home Assistant buttons.** Group/ungroup require the unofficial
Twinkly cloud API, authenticated account access, private source archives, and
multi-step confirmations. A HACS install contains only the HA component, not
the root-level Windows scripts. It would be unsafe to expose working-but-unattended
cloud mutations from HA without a supported credential and recovery architecture.

On the Windows machine with the repository, private `private-area-plan.json`, DPAPI
Twinkly session and immutable 2D/3D archives:

```powershell
git pull

# Read-only, including cloud verification:
python .\area_manager.py verify

# Capture the currently verified grouped 2D Area:
python .\area_manager.py capture-group

# Capture currently standalone 3D controllers from local config:
python .\area_manager.py capture-3d --devices ".\devices.json" --names "Office Left" "Office Right"

# From GROUPED to STANDALONE, read-only preflight first:
python .\area_manager.py ungroup --group "Office Group"

# One-way ungroup, only with explicit acknowledgement and operator checkpoints:
python .\area_manager.py ungroup --group "Office Group" --check-movies --execute --acknowledge-risk --confirm-group "Office Group"

# From STANDALONE to GROUPED, read-only preflight first:
python .\area_manager.py group --group "Office Group"

# One-way group with explicit operator checkpoints:
python .\area_manager.py group --group "Office Group" --execute --acknowledge-risk --confirm-group "Office Group"
```

Do not run both commands automatically. **Never rerun after an ambiguous or partial cloud write**: inspect the private backups and live preflight first. Uploaded movies survived the Office round trip on the tested hardware, but the raw movie RGBW frames remain unbacked up. Group/ungroup is currently limited to **exactly two controllers** with previously verified geometry and cloud metadata.

## Next stage to enable full Group / Ungroup buttons

A dedicated, permissioned cloud executor with a HA-supported account-authentication flow, idempotency protection, operation journal, emergency recovery, and a positive confirmation interaction must be built and tested. It should reuse the same validated Python group operations, not duplicate or modify the built-in Twinkly light integration. Until then, no destructive HA button is registered.

## Security and limitations

- Snapshot and archive contents include device MACs and LED positions; keep HA backups private.
- Effects are identified by UUID/metadata only; their actual RGBW movie frames aren't backed up.
- 3D captures must come from real, independent 3D mappings or separately verified saved references.
- Group capture is **local-only** in HA; cloud validation belongs to the companion CLI.
- Capture and verify actions are safe to repeat; actual grouping changes always require deliberate confirmation.
