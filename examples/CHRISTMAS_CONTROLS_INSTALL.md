# Example V1 / V2 / V3 / Countdown controls

These generic examples provide one programme dropdown, preparation controls, audio-only rehearsal and a Sonos volume slider. V3 is the complete **212-second** programme, *The Elf, the Car and the Fairy Lights*. V1, V2 and Countdown remain available.

`christmas_dashboard_v3.yaml` is a minimal standalone dashboard. Add it as a new dashboard or copy its cards into an existing dashboard; it is not a replacement for a whole-home configuration.

## Requirements and configuration

Install the complete **`christmas_show_elf_chase_v3.mp3`** in the directory served by the audio URL, then update **Twinkly Area Manager to 0.3.36 or later** and restart Home Assistant. Version 0.3.35 deliberately disabled V3 audio and must be updated even when the MP3 is present.

Before installing the example script or package, replace `https://home-assistant.example/local/christmas_lights_on/christmas_audio` with your actual `audio_base` URL, reachable by Sonos. If necessary, replace `media_player.sonos_move` in the script and dashboard with your speaker entity; the show integration's speaker configuration must match.

The dashboard expects `sensor.twinkly_christmas_show_status` and `switch.twinkly_area_manager_maintenance_mode`. Confirm those entity IDs against your installation. The supplied scripts already set `v3_audio_available: true` for the completed release.

## Upgrade from the earlier shared controls

1. Copy the complete V3 MP3 alongside the existing soundtracks, then update the integration to 0.3.36 or later and restart.
2. Keep the existing **`input_select.christmas_show_programme`** helper and its options `V1`, `V2`, `V3`, `Countdown`. Do not create a duplicate helper.
3. Edit the existing **`script.christmas_programme_control`** in YAML and replace its contents with the configured `christmas_programme_control.yaml`. Preserve the entity ID and your actual audio URL/speaker settings.
4. Update the existing show-control dashboard or cards from `christmas_dashboard_v3.yaml`. The pending-audio note has been removed.

If the earlier controls were installed as a package, replace that package with the configured `christmas_show_controls_package.yaml` instead of installing a UI script. You may do this before the integration restart so both changes load together. Do not install both script forms.

## First installation

### A. Through Home Assistant's editors

1. In **Settings → Devices & services → Helpers**, create a **Dropdown** called **Christmas show programme**. Add the exact options `V1`, `V2`, `V3`, `Countdown`; ensure its entity ID is **`input_select.christmas_show_programme`**.
2. In **Settings → Automations & scenes → Scripts**, create one script, open **Edit in YAML**, and paste the configured `christmas_programme_control.yaml`. Ensure its entity ID is **`script.christmas_programme_control`**.
3. Create a new dashboard, open **Edit dashboard → Raw configuration editor**, and paste `christmas_dashboard_v3.yaml`. Alternatively, copy the needed cards into an existing dashboard.

The older `script.christmas_prepare_at_sonos_volume` can remain installed unused. These example cards use the new script ID. Do not paste the package file into the script editor.

### B. As a Home Assistant package

Configure `christmas_show_controls_package.yaml`, then copy it into your existing package directory and load it with the integration restart. It contains both the dropdown and script; do not also create those entities through the UI.

If packages are not yet configured, one standard layout is `/config/packages/christmas_show_controls.yaml`, with this setting under the existing `homeassistant:` block:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Merge the `packages:` line into the existing block. Do not replace `configuration.yaml`, the rest of `homeassistant:`, or `scripts.yaml`. Check the configuration before restarting, then add the example dashboard or copy its cards.

## Programme mapping and audio files

| Dropdown | Integration programme | Soundtrack |
| --- | --- | --- |
| V1 | `original` | `christmas_show_v2.mp3` |
| V2 | `house_chase` | `christmas_show_house_chase_v1.mp3` |
| V3 | `elf_chase_v3` | `christmas_show_elf_chase_v3.mp3` |
| Countdown | Standalone countdown | `landing_countdown_british_v1.mp3` |

Serve the MP3s from the configured `audio_base`, for example `/config/www/christmas_lights_on/christmas_audio/`. Add V3 alongside the existing files; keep V1, V2 and Countdown available. The controls files do not contain audio or the integration.

## Volume and rehearsal

The slider controls the actual speaker volume. Installing the controls sends no volume command. Each full preparation captures the current speaker setting, including zero; the generic script has a 5% fallback only if the volume attribute is absent. Unavailable speakers are blocked for audio programmes.

Choose V3, press **Audio rehearsal**, and adjust the slider while listening. Stop the audio, then **Prepare selected programme**. Wait for Ready and press **GO**. The status sensor supplies the prepared programme's title; cancel before preparing a different selection.

Audio rehearsal plays only the selected MP3 and does not invoke lighting or car preparation. Its Stop control recognizes all four configured soundtrack URLs independently of the dropdown choice and leaves unrelated media alone. Both commands refuse calls while a show is preparing, ready, starting, running or stopping, while status is unavailable, or while audio cleanup is pending. Use normal **Stop / cancel** to retry failed cleanup. All wrapper commands run in one script in single mode.

Quiet lighting rehearsal uses 2% fairy lights and 5% Sonos; Countdown uses 20% digit brightness. Silent lighting rehearsal uses the same dim lighting without audio. V2 and V3 still use house lighting at 100% and include the configured car wake and headlight flash. Sonos must be ungrouped and unmuted for audio programmes.

The dropdown has no fixed `initial:` value, so Home Assistant restores the last selection on restart. A new helper starts at V1; choose V3 for the new programme.

## References

- [Home Assistant dropdown helpers](https://www.home-assistant.io/integrations/input_select/)
- [Native select and volume card features](https://www.home-assistant.io/dashboards/features/)
- [Home Assistant scripts](https://www.home-assistant.io/docs/script/)
