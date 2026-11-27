# Prepare, then Go — Christmas shows

Version **0.3.32** adds separate preparation, a strict physical-button start,
and **Father Christmas and the Cheeky Elf** (`variant: house_chase`). The
original **Santa's Christmas Magic** remains `variant: original` and is still
the default. Both run for 140 seconds. Installation never starts a show.

## Install

Update the integration and restart Home Assistant. Copy the two MP3 files
from the new soundtrack pack into:

```text
/config/www/christmas_lights_on/christmas_audio/
  christmas_show_house_chase_v1.mp3
  landing_countdown_british_v1.mp3
```

Keep `christmas_show_v2.mp3` alongside them for the original show. The updated
standalone countdown uses the British file; the old countdown filename is no
longer used by this integration version. Audio files are supplied separately
from the integration. See the pack's credits and production manifest.

## Prepare on the phone, start with a button

Preparation performs controller authentication, map/effect/movie checks and
black-frame warmup. Sonos silently loads the selected track, pauses, seeks to
zero and restores the selected show volume while paused. **The house, drive
and front-door lights remain as they are.** Only the Twinkly display is held
dark during preparation.

Wait for `sensor.twinkly_christmas_show_status` to show **ready**. Pressing Go
captures the house lights' latest settings, turns those participating lights
off and resumes the already loaded Sonos track. It does not repeat controller
discovery, movie lookup or `play_media`. A short speaker/network response time
still exists; it is not an electrically instantaneous or sample-accurate start.

```yaml
action: twinkly_area_state.prepare_christmas_show
data:
  variant: house_chase
  audio_base: https://home-assistant.example/local/christmas_lights_on/christmas_audio
  brightness_pct: 2
  house_brightness_pct: 100
  volume: 0.05
  offset_ms: 0
```

`house_brightness_pct: 100` is the default, independently of Twinkly brightness,
to make the Hue lights as visible as possible through the shutters. Increase
`brightness_pct` when ready for the outdoor performance. For a dim rehearsal
inside as well, explicitly lower `house_brightness_pct`; undimmable lamps are
excluded below 100%, so they cannot unexpectedly light at full output.

Go requires no settings:

```yaml
action: twinkly_area_state.start_prepared_show
```

This starts whichever programme is ready, including the standalone countdown.
It refuses if no programme is ready. It never silently starts a cold preparation,
changes variants or reloads audio. Changes to settings require another Prepare.
The prepared state is consumed before playback, preventing a second Go from
restarting the running show.

Use `variant: original` with the same Prepare action for the original show.
For the ten-second standalone Landing countdown:

```yaml
action: twinkly_area_state.prepare_countdown
data:
  audio_base: https://home-assistant.example/local/christmas_lights_on/christmas_audio
  brightness_pct: 20
  volume: 0.05
```

Then use the same Go action. `start_christmas_show` and `start_countdown` also
resume their matching prepared programme. When nothing is prepared, these
two actions retain the original prepare-and-start behaviour. The older
`start_countdown_test` name remains an alias.

## Zigbee button example

This is a Home Assistant automation example; installing the integration does
not install or enable it. It assumes this binary sensor produces an `off` →
`on` edge for a press. Check that behaviour in Developer Tools → States before
using it. The status sensor's entity ID may differ if it is renamed or already
exists; substitute its registered ID in the examples if necessary.

```yaml
alias: Christmas show — Zigbee Go
description: Start only the programme already prepared from the phone.
triggers:
  - trigger: state
    entity_id: binary_sensor.zigbee_button_2_binary_input
    from: "off"
    to: "on"
conditions:
  - condition: state
    entity_id: sensor.twinkly_christmas_show_status
    state: ready
actions:
  - action: twinkly_area_state.start_prepared_show
mode: single
max_exceeded: silent
```

## Phone dashboard example

This uses native Home Assistant cards. Adjust the speaker-reachable audio URL
and brightness/volume values in each Prepare action to suit the rehearsal.

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.twinkly_christmas_show_status
    name: Christmas show
  - type: grid
    columns: 2
    square: false
    cards:
      - type: button
        name: Prepare new show
        icon: mdi:home-lightbulb
        tap_action:
          action: perform-action
          perform_action: twinkly_area_state.prepare_christmas_show
          data:
            variant: house_chase
            audio_base: https://home-assistant.example/local/christmas_lights_on/christmas_audio
            brightness_pct: 2
            house_brightness_pct: 100
            volume: 0.05
      - type: button
        name: Prepare original
        icon: mdi:string-lights
        tap_action:
          action: perform-action
          perform_action: twinkly_area_state.prepare_christmas_show
          data:
            variant: original
            audio_base: https://home-assistant.example/local/christmas_lights_on/christmas_audio
            brightness_pct: 2
            volume: 0.05
      - type: button
        name: Prepare countdown
        icon: mdi:timer-outline
        tap_action:
          action: perform-action
          perform_action: twinkly_area_state.prepare_countdown
          data:
            audio_base: https://home-assistant.example/local/christmas_lights_on/christmas_audio
            brightness_pct: 20
            volume: 0.05
      - type: button
        name: Stop / cancel
        icon: mdi:stop
        tap_action:
          action: perform-action
          perform_action: twinkly_area_state.stop_christmas_show
  - type: conditional
    conditions:
      - entity: sensor.twinkly_christmas_show_status
        state: ready
    card:
      type: button
      name: GO
      icon: mdi:play
      tap_action:
        action: perform-action
        perform_action: twinkly_area_state.start_prepared_show
```

## New show sequence

| Soundtrack time | Sequence |
| --- | --- |
| 0–9 s | British Father Christmas welcome; reveal stays dark |
| 9–19 s | Six whole logical bushes, followed by a quicker return pass |
| 19–36 s | All 17 individual window panes |
| 36–44 s | Tree and icicles |
| 44–52 s | Christmas colours and rainbows |
| 52–72 s | Drive → front door → lounge → hall → downstairs loo → bar → office → landing → master bedroom; Twinkly follows the moving light |
| 72–89 s | An authored freeze, dim-down and blackout, followed by Santa's recovery |
| 89–94 s | Countdown invitation and window sweep |
| 94–99 s | Landing window shows 5, 4, 3, 2, 1 with the spoken digits |
| 99–104 s | Eclipse |
| 104–109 s | UpDown |
| 109–114 s | Bright Twinkle |
| 114–119 s | Vertical Flag |
| 119–124 s | Night Sky |
| 124–129 s | Christmas Spirit |
| 129–140 s | Santa's farewell; Christmas Spirit continues |

The freeze is part of the score; it never crashes or reboots Home Assistant,
controllers or Sonos. Each movie name is spoken when selected. The named
movies must be present on all Front controllers: missing or ambiguous labels
fail preparation instead of silently substituting alphabetical alternatives.
The final Christmas Spirit movie continues after the soundtrack until stopped.

House lights are restored to the state captured at Go after completion, stop
or failure. A later explicit Home Assistant user change releases that light
from show control. An unavailable bulb is skipped and reported in the status
sensor's warnings; a room with no available bulbs fails preparation. Colour
bulbs receive RGB; white bulbs receive supported brightness and warm white;
on/off lamps never receive unsupported brightness or colour commands.

## Readiness, cancellation and timing

Status values: `idle`, `preparing`, `ready`, `starting`, `running`, `stopping`, `finished`,
`expired`, `error`. Attributes include programme, variant, warnings and the
last error. Prepared sessions expire after 15 minutes and release their owned
audio and dark Twinkly stream. Stopping before Go leaves the house lights alone.

The speaker must be idle/paused, ungrouped and unmuted for an audible show.
Replacing the paused track invalidates readiness. A prepared stream which
stops, becomes lit, loses ownership or runs short of authentication lifetime
must be prepared again. Stop never stops an unrelated replacement song.

```yaml
action: twinkly_area_state.stop_christmas_show
```

The existing clock correction, signed `offset_ms`, safe stop on a major audio
interruption, and retained final-movie ownership remain in place. Test on the
actual display before a public performance: offline tests cannot establish
real speaker latency, shutter visibility or subjective voice quality.
