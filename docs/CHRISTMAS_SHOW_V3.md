# V3 · The Elf, the Car and the Fairy Lights

V3 is a separate 212-second programme (`variant: elf_chase_v3`). The original
V1 and the first house-chase V2 retain their existing JSON plans and audio
filenames. The shared dashboard selects V1, V2, V3 or the standalone countdown.

## Production status

Version **0.3.36** enables the completed **212-second V3 soundtrack**. All 31
narration phrases use the same Nathaniel voice to portray Father Christmas,
checked against their submitted text and native timing, and mixed with the
music. The choreography, runtime and shared controls have passed offline tests.
V1, V2 and Countdown remain available with their existing audio files.

Install `christmas_show_elf_chase_v3.mp3` before using audible V3. The current
control script enables V3 with `v3_audio_available: true`. An older 0.3.35
installation deliberately disabled V3 while its soundtrack was incomplete;
update both the integration and those controls when upgrading.

Audio rehearsal plays only the soundtrack. An explicitly selected **silent
lighting rehearsal** runs the choreography without Sonos and includes the
house lights and car.

## What changes for the audience

The elf's golden light travels through the house. Ice-white fairy lights follow
the corresponding frontage windows. Each room holds for four seconds; the
elf sometimes gets two rooms ahead, and the middle run lets him complete three
rooms before the fairy lights start following. A hiding glimpse and a clear
find finish the chase. This is a schematic route using the existing room and
pane mappings, not a new physical map of the house.

The narration calls them **fairy lights**. The car moment comes before the elf
enters the house, and the finale uses delighted reactions instead of speaking
the stored movies' technical names. The voice is ElevenLabs' **Nathaniel – Deep,
Rich and Mature**, a deep British narrator generated with Eleven v4 through
fal.ai. V3 uses complete independent phrases at their generated pitch and speed,
with preserved word endings and space between them; it does not reuse the old
timestamp-based cuts that truncated the end of “magic”.

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

The same audible controls apply to V1, V2, V3 and Countdown once their selected
soundtrack is installed. V3's silent rehearsal also remains available.

The script refuses audio controls while a show is preparing, ready, starting,
running, stopping or unavailable. It also checks the speaker is available,
ungrouped and unmuted before loading a soundtrack. Audio-stop only stops a
recognized show soundtrack, so it cannot stop unrelated music.

## Install the completed V3 soundtrack

Copy the completed V3 MP3 from the audio pack to:

```text
/config/www/christmas_lights_on/christmas_audio/christmas_show_elf_chase_v3.mp3
```

Keep the other three MP3s. Install Twinkly Area Manager **0.3.36 or later** and
restart Home Assistant, then install the current controls. If retaining the
0.3.35 control script, set `v3_audio_available: true` after installing the MP3
and integration update, and remove its old soundtrack-pending dashboard note.
Update only the script or package installation method you use. The integration
update and dashboard installation do not start a show. Rehearse the finished
audio before preparing a full lighting performance.

## Validation and limits

The offline simulation uses the real scheduler and scene code with fake
Home Assistant, Sonos, car and light I/O. It checks the selected track, silent
preparation, the full 212-second timeline, one car command at 58 seconds, six
movie changes, programme guards and restoration. Existing V1/V2 tests remain
part of the suite. Dashboard parsing and template routing are separate checks.
Full V3 simulations now use the released plan directly. A separate in-memory
unavailable-soundtrack fixture retains the regression that audible Prepare and
Start must fail before any device writes when a score is marked unavailable.

The completed audio was checked independently of those device simulations.
All 31 phrases match the selected voice/model/settings and native timing;
independent speech recognition also checked their spoken content. Every source
tail is retained, including the three “magic” endings, and the spoken countdown
matches its five light cues. The master is **212.000 seconds**; the encoded MP3
container reports **212.040 seconds**. Delivery is **48 kHz stereo, 320 kbps**.

Peak headroom constrains the final mastering gain: the completed mix measures
**−19.43 LUFS integrated**, **−2.02 dBTP true peak** and **6.0 LU loudness range**.
Source hashes, per-phrase timing, retained endings and mastering measurements
are recorded in `production_manifest_v3.json` supplied with the audio pack.
Speech was not time-stretched or pitch-shifted to fit a slot. Floating-point
intermediate audio preserves headroom before final mastering.

The released `christmas_show_elf_chase_v3.mp3` has SHA-256:

```text
86c2dbd97f8bdff78431bcfd7413cd8cec61e9809c1f4428d7c3f558e587adcf
```

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

Synthetic narration uses [Nathaniel – Deep, Rich and Mature](https://elevenlabs.io/app/voice-library?voiceId=7S3KNdLDL7aRgBVRQb1z),
voice ID `7S3KNdLDL7aRgBVRQb1z`, through `elevenlabs/tts/eleven-v4` on fal.ai.
The voice and model identities are recorded with every phrase in the audio manifest.
