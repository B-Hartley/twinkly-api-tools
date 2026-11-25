# Santa's Christmas Magic — version 2

A 140-second Christmas show for the existing Front display, with a continuous
musical score, Santa narration, 25 logical light zones and a stored-movie finale.
Integration version **0.3.24** is paired with **christmas_show_v2.mp3**. The older
115-second, twelve-track soundtrack does not match this choreography.

## Install the audio and integration together

1. Download the version 2 soundtrack pack and copy `christmas_show_v2.mp3` to
   the Home Assistant folder
   `/config/www/christmas_lights_on/christmas_audio` (or its `www` Samba share).
2. Update Twinkly Area Manager from this repository's `main` branch in HACS,
   then restart Home Assistant.
3. Confirm that the Sonos Move is idle or paused and is not grouped with other
   speakers. The file must be available to it at:
   `https://home-assistant.example/local/christmas_lights_on/christmas_audio/christmas_show_v2.mp3`.
   Replace `home-assistant.example` with the address reachable by your speaker.
4. Start the show explicitly from Developer Tools → Actions. Installation and
   restart never start it automatically.

A quiet first rehearsal:

```yaml
action: twinkly_area_state.start_christmas_show
data:
  audio: true
  audio_base: https://home-assistant.example/local/christmas_lights_on/christmas_audio
  brightness_pct: 2
  volume: 0.05
  offset_ms: 0
```

For a brighter rehearsal, adjust `brightness_pct` and `volume` to suit the display. Brightness scales the whole score, including native movie playback.
An intentional silent lighting rehearsal uses `audio: false`. Setting brightness
to zero keeps every zone black and does not start a stored movie.

To stop the running show or its retained final movie:

```yaml
action: twinkly_area_state.stop_christmas_show
```

## What the audience sees and hears

| Time | Santa and soundtrack | Lights |
| --- | --- | --- |
| 00:00–00:09 | Santa's welcome over the gentle opening | Prepared display remains dark for the reveal |
| 00:09–00:19 | “Bushes… wake up!” | Six bushes individually, solid colours; a quicker return pass |
| 00:19–00:32 | “Windows… it's your turn to shine!” | All 17 panes individually, then a few balanced pairs |
| 00:32–00:36 | Christmas tree cue | Tree alone |
| 00:36–00:42 | Icicle cue | Icicles alone |
| 00:42–00:59 | Colours unite; full arrangement enters at 00:46.72 | Several spatial red/green/gold/blue arrangements |
| 00:59–01:14 | “Rainbows… fill the windows…” | Four waves introduce a rainbow inside every pane; direction changes follow |
| 01:14–01:28 | Golden sparkle travels around the house and back | A complete 17-pane outward and return chase, timed to the music |
| 01:28–01:42 | Bushes join, then Santa calls the windows at 01:30.96 | Six bushes, tree, icicles, all 17 panes, then a gold build |
| 01:42–01:59 | First finale phrase | First stored movie |
| 01:59–02:10 | Second finale phrase and natural musical ending | Second stored movie when available |
| 02:10–02:20 | Santa's farewell | Last stored movie continues |

Front Left and Back Right each remain one complete bush. Front Right remains
**two separate bushes**, using its existing left/right pane mapping. The tree
and icicles are not counted as bushes. The route follows the configured logical
window and garden order; the supplied preview is a schematic, not a surveyed
plan of the garden.

## How synchronization works

The integration checks controller identities, pane maps, every requested effect,
all timeline scenes and the shared stored-movie catalogue before the soundtrack.
It then turns controllers off, resets native brightness while dark, and completes
the realtime black-frame warmup. Controller authentication and the two-second
warmup therefore cannot consume the first spoken cues.

Sonos receives **one** `play_media` call. The show waits for the new, versioned
file URI and a fresh playback position, then runs the entire score against one
monotonic clock. Every cue sharing a timestamp is committed in one scene and
one completed frame per participating controller; a bush-off/window-on change
has no intervening hardware shutdown or startup.

Narration stems use the same absolute start times as the score. The delivered
HeyGen Santa take has character and word timestamps; it was split at phrase
boundaries and placed without pitch shifting or time stretching. The recording
of “We Wish you a Merry Christmas” is continuous, with no musical cuts. Music
levels gently fall under narration and recover between phrases.

This is **not sample-accurate playback**. Home Assistant exposes whole-second
Sonos positions, and actual speaker buffering and controller/network latency
must be checked on the installed display. Use the same opening bush cue to set
`offset_ms` after a local rehearsal:

- Positive values delay lights when they are visibly early.
- Negative values advance lights when they are visibly late.
- Range: −2000 to +2000 milliseconds. Start at zero and adjust in small steps.

The show stops if the soundtrack is replaced, pauses before its natural end,
loses its usable position, or a meaningful cue becomes more than 1.5 seconds
late. It does not rush through obsolete cues or silently run a different show.
A normal Sonos end reported as paused is recognized. The full audio tail is
preserved even when a negative light offset is used.

## Stored movies and stopping

At least one movie UUID must be shared across all Front controllers. Local
numeric movie IDs can differ; the integration resolves each controller's ID
before audio begins. Movies are selected in stable alphabetical label order.
The two slots choose the first and second shared movies. When only one exists,
it continues through the second slot without restarting.

The final movie remains playing after the soundtrack. The stop action retains
ownership of that finale, so it still turns it off after the timed show has
finished. A later manual replacement releases the old finale's ownership.
Cancellation stops owned audio promptly, turns the show's controllers off, and
restores the previous Sonos volume when the speaker has not been taken over.
Normal last-light-off behavior outside an explicitly running show is unchanged.

Stored movies run on the controllers' own clocks. Their **start cues** are timed
to musical phrases; their internal frames are not rewritten to match this music.

## Music and voice credits

“We Wish you a Merry Christmas” by Kevin MacLeod (incompetech.com).
Recording licensed under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
[Artist track page](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100369).
Changes: Santa narration, narration-led level ducking, gain adjustment, fades
and mastering. The musical arrangement is retained continuously. No endorsement
by the artist is implied.

Santa narration: HeyGen's **Santa - Public** synthetic character voice. This is
an AI voice performance, not a human voice-actor recording. The delivered voice
uses its natural character and accent without artificial pitch lowering.

The downloadable mix is stereo, 48 kHz, 320 kbps MP3. Its 24-bit WAV counterpart
is the uncompressed mix master; the licensed music source itself is a 44.1 kHz,
320 kbps MP3. Full source credits and measured audio quality are in the pack.

## Offline editing and rebuilds

Runtime playback has no added Python dependencies. Authoring tools are separate
and require Python, ffmpeg, and numpy. Music and voice assets are provided in the
production pack rather than added to the integration or public source history.

- `tools/christmas_narration.json` is the script and absolute speech schedule.
- `tools/christmas_music_grid.json` holds the measured musical beats.
- `tools/build_christmas_plan.py` writes the installed JSON score.
- `tools/split_santa_narration.py` splits one timestamped HeyGen take into stems.
- `generate_christmas_audio.py` builds the continuous mix from music and stems.
- `tools/render_christmas_preview.py` renders an illustrative timing preview.

Rebuild the installed score:

```bash
python tools/build_christmas_plan.py
```

Rebuild the soundtrack with the supplied assets:

```bash
python generate_christmas_audio.py \
  --music production/music/we_wish_you_a_merry_christmas.mp3 \
  --voice-dir production/voice \
  --output christmas_audio
```

Any script, voice or timing change must be followed by a new mix and timing
check. Do not regenerate narration during live playback. The optional
`tools/render_santa_voice.py` produces an offline Kokoro draft; it is not the
HeyGen voice in the delivered version 2 soundtrack.

## Verification boundary

The automated tests include a complete 140-second simulation through the real
Show, ShowLights, entity classes, pane maps and pixel renderer with simulated
Sonos and controller transport. They cover the six-bush/17-pane introductions,
all pane rainbows, atomic transitions, movie selection, missing maps, audio
interruptions, signed offsets, cancellations and stopping the retained finale.
They do not replace a local audiovisual rehearsal on the actual display.
