# Example V1 / V2 / V3 / Countdown controls

These generic examples provide one programme dropdown, preparation controls, audio-only rehearsal, and a Sonos volume slider. `christmas_dashboard_v3.yaml` is a minimal standalone dashboard. Add it as a new dashboard, or copy its show-control cards into an existing dashboard; it is not a replacement for an existing whole-home configuration.

> **V3 soundtrack pending:** these controls support V1, V2 and Countdown with their existing audio. V3 is currently available only as silent lighting choreography. Its audio preparation and rehearsal are disabled until the completed soundtrack and a matching audio-enabled integration release are installed.

## Configure the examples

Before installation, edit the script's `audio_base` from `https://home-assistant.example/local/christmas_lights_on/christmas_audio` to a URL reachable by your Sonos speaker. If necessary, replace `media_player.sonos_move` in both the script and dashboard with your speaker entity. The show integration's speaker configuration must match.

The dashboard expects the integration's `sensor.twinkly_christmas_show_status` and `switch.twinkly_area_manager_maintenance_mode` entities. Confirm those names against your installation. These controls require the integration update that adds the `elf_chase_v3` variant.

## Choose one installation method

### A. Through Home Assistant's editors

1. In **Settings → Devices & services → Helpers**, create a **Dropdown** called **Christmas programme**. Add these four options exactly, in order: `V1`, `V2`, `V3`, `Countdown`. Edit its entity ID to **`input_select.christmas_show_programme`**.
2. In **Settings → Automations & scenes → Scripts**, create a new script, open **Edit in YAML**, and paste the configured `christmas_programme_control.yaml`. Save it and ensure the entity ID is **`script.christmas_programme_control`**.
3. Create a new dashboard, open **Edit dashboard → Raw configuration editor**, and paste `christmas_dashboard_v3.yaml`. Alternatively, copy the needed cards into an existing dashboard.

An older `script.christmas_prepare_at_sonos_volume` may remain installed unused. These example cards use the new script ID. Do not paste the package file into the script editor.

### B. As a Home Assistant package

Configure `christmas_show_controls_package.yaml` as described above, then copy it into your existing package directory and restart Home Assistant. It contains both the dropdown and script; do not also create those same entities through the UI.

If packages are not yet configured, one standard layout is `/config/packages/christmas_show_controls.yaml`, with this entry under the existing `homeassistant:` configuration:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Merge the `packages:` line into the existing `homeassistant:` block. Do not replace `configuration.yaml`, an existing `homeassistant:` block, or `scripts.yaml`. Check the configuration before restarting. Then add the example dashboard or copy its cards.

## Programme mapping and audio files

| Dropdown | Integration programme | Soundtrack |
| --- | --- | --- |
| V1 | `original` | `christmas_show_v2.mp3` |
| V2 | `house_chase` | `christmas_show_house_chase_v1.mp3` |
| V3 | `elf_chase_v3` | `christmas_show_elf_chase_v3.mp3` |
| Countdown | Standalone countdown | `landing_countdown_british_v1.mp3` |

Keep the existing V1, V2 and Countdown MP3s in the directory served by the configured `audio_base`, for example `/config/www/christmas_lights_on/christmas_audio/`.

### Enable V3 audio after its completed release

Once the complete `christmas_show_elf_chase_v3.mp3` and its matching audio-enabled integration release are supplied:

1. Add that MP3 alongside the existing audio files.
2. Install the matching audio-enabled integration release and restart Home Assistant. Version 0.3.35 deliberately marks V3 audio unavailable; copying a file alone does not enable it.
3. In the installed script or package, change **`v3_audio_available: false`** to **`v3_audio_available: true`**. Save the UI script, or restart Home Assistant if using the package method. Update only the installation method you chose.
4. Remove or update the dashboard's **V3 soundtrack pending** Markdown card, then use audio rehearsal before preparing the full V3 show.

The flag blocks V3 full/quiet preparation and audio rehearsal, while leaving V3 silent lighting rehearsal and all existing programmes available. Audio stop still recognizes all four soundtrack URLs. Do not substitute incomplete narration clips for the final track.

## Volume and rehearsal

The slider controls the actual speaker volume. Installing the controls sends no volume command. Each full performance preparation captures the current speaker setting, including zero; the generic script has a 5% fallback only if the volume attribute is absent. Unavailable speakers are blocked for audio programmes.

Choose V1, V2 or Countdown while V3 audio is pending, press **Audio rehearsal**, and adjust the slider while listening. Press **Stop audio rehearsal**, then **Prepare selected programme**. Wait for Ready, then press **GO**. GO starts the loaded programme, whose title comes from the integration's status sensor. Cancel before preparing a different programme.

Audio rehearsal plays only the selected MP3. It does not invoke lighting or car preparation. Its Stop control checks all four configured soundtrack URLs, independently of the current dropdown choice, and leaves unrelated speaker media alone. Both commands refuse calls while the show is preparing, ready, starting, running or stopping, while show status is unavailable, or when audio cleanup is pending. Use the normal show **Stop / cancel** control to retry failed cleanup. All three wrapper commands run in one script in single mode.

Quiet lighting rehearsal uses 2% fairy lights and 5% Sonos; Countdown uses 20% digit brightness. Silent lighting rehearsal uses the same dim lighting without Sonos playback. V2 and V3 still use house lighting at 100% and include the configured car wake and headlight flash. Sonos must be ungrouped and unmuted for audio programmes.

The dropdown has no fixed `initial:` value, so Home Assistant restores its last selection on restart. A new helper starts at V1; select V3 for the new programme.

## References

- [Home Assistant dropdown helpers](https://www.home-assistant.io/integrations/input_select/)
- [Native select and volume card features](https://www.home-assistant.io/dashboards/features/)
- [Home Assistant scripts](https://www.home-assistant.io/docs/script/)
