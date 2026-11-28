# Visitor colour match (0.3.41)

The new **Visitor colour match** switch defaults off after every reload/restart.
Enable it only during the intended activity. It listens to
`event.drive_zigbee_button_2_button`; only a recognized press triggers it,
with a two-second debounce and no concurrent button sequences. A prepared or
running Christmas show always has priority; no changes to the existing GO
automation are required. The existing GO automation may still report that
nothing is prepared if it is unconditional.

The switch is off by default because each request sends a fresh Approach
camera image to the configured Home Assistant OpenAI AI Task provider.
No image files are written by this feature. Provider processing/retention
and charges follow the existing AI integration's settings. The model is
asked only for one or two standout colourful items worn or carried by visitors,
including dogs, dog coats, hats, scarves and accessories, not identity or other
personal characteristics; camera text is not trusted. A vivid accessory beats a
large neutral coat; if only neutral colours are present, use at most one distinctive
item (such as a brown dog). When the image is infrared, monochrome or colours are unreliable, the same AI request can instead return reliable counts of people and dogs. If neither genuine colours nor reliable nonzero counts are available, the normal fallback applies. The driveway boundary is interpreted by the AI,
not a calibrated region mask.

The six bush zones are assigned alternating standout colours at 65% brightness.

**Night mode:** One bush per visible person or dog, each person a festive colour, dogs light blue. Unused bush zones are switched off, so the number lit matches the announced count. A group larger than six lights all six bushes with a truthful group announcement. Counts are based on a single snapshot, not identification or tracking. No second camera request or AI analysis call is needed; the structured response includes both counts and colour information.
Colours stay until another light command, including a later show. Black is
represented by a dim charcoal RGB approximation, not literally black light.
The switch controls future matching, not the current colour of the lights.
Turning it off discards a pending analysis result; already-issued commands
cannot be undone. A successful match can replace a native movie with virtual bush colours.

Choose **Approach speaker**, **Doorbell speaker** or **Sonos Move** from
the new announcement speaker select. The Approach speaker is the default;
the last selection is restored after restart. Camera speakers use standard
Home Assistant TTS at their configured volume. Sonos uses its announcement
overlay at 35% temporary volume. An unavailable or muted selected speaker
never falls back to another device. A failed light command prevents the
announcement but may leave earlier bushes changed. No retry is attempted on AI failure.

The [dedicated dashboard view](../examples/visitor_colours_dashboard_view.yaml)
includes the enable switch, speaker selector, camera, manual match and
audio-only test actions, show status and six bush controls. Paste the view
into the existing Christmas Lights dashboard's views list (indent as
appropriate). The audio-only test does not need the enable switch on, does
not analyse an image, and refuses to interrupt an active or prepared show.

## Dashboard tile

```yaml
type: tile
entity: switch.twinkly_area_manager_visitor_colour_match
name: Match visitor clothing
icon: mdi:palette
```

Check the generated entity ID in your entity registry. The manual action
`twinkly_area_state.match_visitor_colours` also requires the switch to be on.

## Commissioning

1. Install/reload the integration, leave the switch off, and confirm GO
   still starts a prepared show.
2. Stop the show. Enable the switch and set the operating hours to include the test time.
3. In colour-camera conditions stand in the driveway wearing a known red coat
   and press the physical button. Check all six bush zones and the selected speaker
   announcement. Verify the button's actual event_type in Developer Tools;
   if it is not recognized, update PRESS_TYPES.
4. Try a bright scarf with a dull coat, a dog coat, two contrasting colourful items,
   no visitors, and an IR-only image with three people and one dog. Check that four bushes illuminate and the announcement matches. Empty or uncertain scenes must not produce a personalised announcement; the normal random fallback remains available.
5. Turn the switch off during a request and confirm no new commands after
   any already-in-flight light command completes. Test maintenance mode and
   prepared-show precedence.

Installation-specific defaults are in visitor.py. Camera AI, physical
button events, lighting and all speakers must be validated on the actual HA system.


## Button routing and timing

A prepared Christmas programme retains priority via the existing GO automation.
During preparation, playback, maintenance or mapping, the visitor controller
does nothing. Otherwise a button press attempts AI matching only when enabled,
inside its local Home Assistant time window, and outside the cooldown. Defaults
are all day within the master hours and 10 minutes. Change them on the dashboard (15-minute time
increments; 0–60 minute cooldown; 0 means no cooldown; equal start and end
means all day). The end is exclusive and overnight windows are supported.
Cooldown is measured from the last AI attempt, persisted across restart; it
is not facial recognition or group tracking. An AI failure also consumes the
cooldown. The manual match action respects the same settings and has no fallback.

If matching is disabled, ineligible or fails before lights are fully applied,
the enabled fallback randomly chooses an available mapped Front chase (Window
Chase, House and Garden Chase or Bush and Window Chase V2), avoids immediately
repeating it if alternatives exist, runs for seven seconds, then advances to
the next shared stored movie in alphabetical order, wrapping around. If no
chase is mapped it uses a solid colour; if no shared movies exist it ends on
a solid colour. Movie selection is from the live catalogue, not a hardcoded
list. The next press advances the movie again; no background movie timer.
A successful visitor match remains lit until the next lighting command.
The AI time restriction applies to AI only; the fallback is independently
switchable. The fallback switch defaults on and is restored after restart.
Both paths respect maintenance and the show lifecycle lock.

The original GO automation may log a no-ready-programme error if it has no
ready-state condition; set its condition to show status ready. Do not add
a second automation invoking visitor_button for the physical button, because
the integration already listens for it. The manual dashboard test invokes
visitor_button without a physical press.

## Master operating schedule

Master enable and two independent windows gate both AI matching and silent button fallbacks, including manual match/full-button tests. Defaults: morning 06:30–08:30, evening 16:30–18:30, local Home Assistant time. The settings restore after restart; the existing extra AI time restriction is retained on upgrades (set its endpoints equal to allow AI during both master periods). Equal master endpoints disable that period; overnight windows are supported. The master switch can disable all visitor button actions. A prepared Christmas show retains priority and is not subject to these hours. No automatic light switch-on is scheduled. Existing lights or stored movies are not forcibly switched off at a window boundary; this is an interaction schedule, not a house lighting timer. A flourish ending outside the window will not start a new movie. Explicit audio-only speaker testing remains independent.
