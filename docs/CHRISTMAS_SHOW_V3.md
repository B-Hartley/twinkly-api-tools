# V3 · The Elf, the Car and the Fairy Lights

V3 is a separate 212-second programme (`variant: elf_chase_v3`). The original
V1 and the first house-chase V2 retain their existing JSON plans and audio
filenames. The shared dashboard selects V1, V2, V3 or the standalone countdown.

## Production status

The choreography, runtime and shared controls have passed offline tests.
The V3 soundtrack is **not yet released**; no complete V3 MP3 is supplied.
V1, V2 and Countdown remain available with their existing audio files.

This release marks V3's soundtrack unavailable. The integration refuses an
audible V3 Prepare or Start before changing the lights, speaker or car. The
control script also blocks V3 audio rehearsal and audible preparation with
`v3_audio_available: false`. An explicitly selected **silent lighting rehearsal**
can still run the V3 choreography; it includes the house lights and car.

Audible V3 requires the completed, checked soundtrack and a future matching
integration release that enables it. Copying a preview or changing only the
script flag cannot make this release prepare audible V3. Do not bypass the
runtime availability flag.

## What changes for the audience

The elf's golden light travels through the house. Ice-white fairy lights follow
the corresponding frontage windows. Each room holds for four seconds; the
elf sometimes gets two rooms ahead, and the middle run lets him complete three
rooms before the fairy lights start following. A hiding glimpse and a clear
find finish the chase. This is a schematic route using the existing room and
pane mappings, not a new physical map of the house.

The narration calls them **fairy lights**. The car moment comes before the elf
enters the house, and the finale uses delighted reactions instead of speaking
the stored movies' technical names. The selected voice is a naturally deep,
older British Santa-style voice. V3 is authored as complete independent
phrases, with preserved word endings and space between them; it does not reuse
the old timestamp-based cuts that truncated the end of “magic”.

| Time | Scene |
| --- | --- |
| 0:00–0:52 | Santa welcomes the audience; bushes, panes, tree, icicles and colours |
| 0:52–1:04 | The elf tries the car; driveway/headlights cue at 0:58 |
| 1:04–1:30 | Front door → lounge → hall → downstairs loo; fairy lights start eight seconds later |
| 1:30–1:54 | Bar → office → landing; fairy lights start twelve seconds later |
| 1:54–2:14 | Bedroom → landing → office; fairy lights start eight seconds later |
| 2:14–2:30 | Hide, search and find; both lights meet in the bedroom at 2:22 |
| 2:30–2:41 | Big-finale announcement and five-to-one Landing countdown |
| 2:41–3:17 | Six existing stored movies, changing every six seconds with a spoken reaction |
| 3:17–3:32 | Farewell while the final movie continues |

The final movie remains on until Stop / cancel. House lights and the speaker's
preparation-time volume are restored at cleanup as before.

## Jyggy

The existing Jyggy behavior is reused: preparation sends `button.jyggy_wake`
in the background, and the first driveway turn-on sends one
`button.jyggy_flash_lights` command after the wake call succeeds. For V3 this
driveway cue is at 58 seconds. The car is never commanded to drive or unlock.
The external car service has its own response delay; the scheduler's command
time is not a promise of sample-accurate physical headlights.

Car participation requires the existing wake/flash entities and house brightness
100%. A failed or still-pending wake is skipped. Audio-only rehearsal never
prepares the house or car. Quiet and silent lighting rehearsals retain full
house brightness, so V2/V3 lighting rehearsals still include Jyggy.

## Shared controls and initial volume

Use the [generic installation guide](../examples/CHRISTMAS_CONTROLS_INSTALL.md), with:

- [Minimal Show Control dashboard example](../examples/christmas_dashboard_v3.yaml)
- [Standalone UI script](../examples/christmas_programme_control.yaml)
- [Alternative package containing helper and script](../examples/christmas_show_controls_package.yaml)

The dropdown is `input_select.christmas_show_programme`; the single control
script is `script.christmas_programme_control`. Existing scripts can remain.

| Selection | Runtime variant/action | Soundtrack |
| --- | --- | --- |
| V1 | `original` | `christmas_show_v2.mp3` |
| V2 | `house_chase` | `christmas_show_house_chase_v1.mp3` |
| V3 | `elf_chase_v3` | `christmas_show_elf_chase_v3.mp3` |
| Countdown | `prepare_countdown` | `landing_countdown_british_v1.mp3` |

The dashboard's volume slider is the actual Sonos Move volume. Installation
sends no volume command; performance preparation reads the current value,
including zero, each time. Quiet preparation deliberately uses the existing
5% Sonos preset. Adapt the example's speaker entity and audio base URL for
your installation. A user's complete personal dashboard belongs in their
private Home Assistant configuration, outside this repository.

For an audio-only rehearsal, choose the programme, press Audio rehearsal and
adjust the slider while listening. Stop audio rehearsal, then Prepare selected
programme. Wait for Ready and press Go or the existing physical GO button.
The loaded programme is kept in the runtime's prepared session; changing a
selector cannot change what an already-prepared Go starts.

These audible controls currently apply to V1, V2 and Countdown. Selecting V3
shows that its soundtrack is pending; its silent rehearsal remains available.

The script refuses audio controls while a show is preparing, ready, starting,
running, stopping or unavailable. It also checks the speaker is available,
ungrouped and unmuted before loading a soundtrack. Audio-stop only stops a
recognized show soundtrack, so it cannot stop unrelated music.

## Install the completed V3 soundtrack

Once the complete audio pack and matching audio-enabled integration release
are published, copy the final MP3 to:

```text
/config/www/christmas_lights_on/christmas_audio/christmas_show_elf_chase_v3.mp3
```

Keep the other three MP3s. Install the matching integration update and restart
Home Assistant. Version 0.3.35 keeps V3 audio disabled; it is not the completed
audio release. Then enable `v3_audio_available` in the installed control script
or use the updated controls supplied with that release. The integration update
and dashboard installation do not start a show. Rehearse the finished audio
before preparing a full lighting performance.

## Validation and limits

The offline simulation uses the real scheduler and scene code with fake
Home Assistant, Sonos, car and light I/O. It checks the selected track, silent
preparation, the full 212-second timeline, one car command at 58 seconds, six
movie changes, programme guards and restoration. Existing V1/V2 tests remain
part of the suite. Dashboard parsing and template routing are separate checks.
The shipped V3 plan is tested to reject audible Prepare and Start without any
device writes. Full audible V3 simulations explicitly enable only their fake
in-memory fixture; those simulations are not evidence that a final MP3 exists.

No test claims live Sonos buffering, car wake timing, sightlines through the
windows or actual controller synchronization. Use audio rehearsal first when
the completed track is installed, then a local lighting rehearsal.

## Music attribution

“We Wish you a Merry Christmas” by Kevin MacLeod (incompetech.com), licensed
under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
[Artist track page](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100369).
V3 extends the instrumental score with a chase repeat and crossfades, adds
synthetic Santa narration and uses narration-led ducking and mastering. No
endorsement is implied.
