# Visitor colour match (0.3.38)

The new **Visitor colour match** switch defaults off after every reload/restart.
Enable it only during the intended activity. It listens to
`event.drive_zigbee_button_2_button`; only a recognized press triggers it,
with an eight-second debounce and no concurrent analyses. A prepared or
running Christmas show always has priority; no changes to the existing GO
automation are required. The existing GO automation may still report that
nothing is prepared if it is unconditional.

The switch is off by default because each request sends a fresh Approach
camera image to the configured Home Assistant OpenAI AI Task provider.
No image files are written by this feature. Provider processing/retention
and charges follow the existing AI integration's settings. The model is
asked only for clothing colours and garment types, not identity or other
personal characteristics; camera text is not trusted. It returns up to three
allowlisted colours. No reliable colours (including IR-only images) means
no lighting or announcement. The driveway boundary is interpreted by the AI,
not a calibrated region mask.

The six bush zones are assigned alternating dominant colours at 65% brightness.
Colours stay until another light command, including a later show. Black is
represented by a dim charcoal RGB approximation, not literally black light.
The switch controls future matching, not the current colour of the lights.
Turning it off discards a pending analysis result; already-issued commands
cannot be undone. Existing native movie playback blocks matching to avoid
unexpectedly changing the other lights in a shared physical group. Stop the
movie and use the virtual lights before enabling the feature.

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
2. Stop the show and any native movie. Enable the switch.
3. In colour-camera conditions stand in the driveway wearing a known red coat
   and press the physical button. Check all six bush zones and the selected speaker
   announcement. Verify the button's actual event_type in Developer Tools;
   if it is not recognized, update PRESS_TYPES.
4. Try two contrasting coats, no visitors, and an IR-only image. Check that
   empty or uncertain scenes do not change lights or speak.
5. Turn the switch off during a request and confirm no new commands after
   any already-in-flight light command completes. Test maintenance mode and
   prepared-show precedence.

Installation-specific defaults are in visitor.py. Camera AI, physical
button events, lighting and Sonos must be validated on the actual HA system.
