#!/usr/bin/env python3
"""Build the 140-second light score against the measured Christmas soundtrack.

This is an offline authoring tool. It never contacts Home Assistant or lights.
Narration, music and light timing all use the same absolute seconds; chapters
are simply an editing aid, not separate playback requests.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "light.twinkly_"
FRONT = PREFIX + "front"
WINDOWS = [PREFIX + name for name in (
    "office_left_pane_1", "office_left_pane_2", "office_left_pane_3",
    "office_right_pane_1", "office_right_pane_2", "office_right_pane_3",
    "master_bedroom_left", "master_bedroom_right", "landing_pane_1", "landing_pane_2",
    "lounge_pane_1", "lounge_pane_2", "lounge_pane_3",
    "bar_pane_1", "bar_pane_2", "bar_pane_3", "bar_pane_4",
)]
BUSHES = [PREFIX + name for name in (
    "front_left_bush", "back_left_bush", "back_right_bush", "mid_right",
    "front_right_left_bush", "front_right_right_bush",
)]
TREE, ICICLES = PREFIX + "light_tree", PREFIX + "icicles"
GARDEN = BUSHES + [TREE, ICICLES]
ALL = WINDOWS + GARDEN
# Physical routes are separate from palette groupings.
BUSH_ROUTE = [BUSHES[i] for i in (0,4,5,3,2,1)]
WINDOW_ROUTE = [WINDOWS[i] for i in (10,11,12,13,14,15,16,5,4,3,2,1,0,9,8,7,6)]
GARDEN_ROUTE = [BUSH_ROUTE[0], TREE] + BUSH_ROUTE[1:] + WINDOW_ROUTE[:7] + [ICICLES] + WINDOW_ROUTE[7:]
RED, GREEN, GOLD, BLUE, PURPLE, ICE = (
    [255, 25, 10], [10, 230, 50], [255, 165, 35], [30, 90, 255],
    [175, 35, 255], [140, 210, 255],
)


def build():
    grid = json.loads((ROOT / "tools/christmas_music_grid.json").read_text())
    narration = json.loads((ROOT / "tools/christmas_narration.json").read_text())["narration"]
    beats = grid["beats"]
    events = []

    def cue(at, targets, **data):
        events.append({"absolute": round(at, 4), "targets": list(targets), **data})

    def off(at):
        cue(at, [FRONT], off=True)

    def solid(at, targets, rgb, brightness=150):
        cue(at, targets, brightness=brightness, rgb=rgb)

    def solo(at, target, rgb=GOLD, brightness=150):
        # These two operations are one atomic frame in ShowLights.
        off(at)
        solid(at, [target], rgb, brightness)

    def nearest(at):
        return min(beats, key=lambda beat: abs(beat - at))

    off(0)
    palette = [RED, GREEN, GOLD, BLUE, PURPLE, GREEN]
    for at, target, color in zip(grid["bush_solo_beats"], BUSH_ROUTE, palette):
        solo(at, target, color, 115)
    # A second, quicker solo pass keeps every bush separate until the windows.
    for index, at in enumerate([15.7431, 16.2656, 16.8112, 17.3105, 17.8329, 18.367]):
        solo(at, BUSH_ROUTE[::-1][index], palette[(index + 2) % 6], 125)

    for index, (at, target) in enumerate(zip(grid["window_solo_beats"], WINDOW_ROUTE)):
        solo(at, target, palette[index % len(palette)], 130)
    # Extra individual panes on the remaining four spoken beats.
    for at, index, color in [
        (27.921, 0, RED), (28.9321, 5, GREEN),
        (29.8725, 10, GOLD), (30.8361, 16, BLUE),
    ]:
        solo(at, WINDOW_ROUTE[index], color, 140)

    solo(32.299, TREE, GOLD, 150)
    solid(nearest(33.7), [TREE], GREEN, 170)
    solid(nearest(34.65), [TREE], GOLD, 190)
    solo(36.4553, ICICLES, ICE, 140)
    for index, at in enumerate([37.8021, 38.6961, 39.5784, 40.4027]):
        solid(at, [ICICLES], ICE if index % 2 else GOLD, 170 if index % 2 else 110)

    # Musical phrases use distinct spatial arrangements, without strobing.
    for phrase, requested in enumerate([42.0629, 44.21, 46.7185, 49.71, 52.70, 55.68, 57.19]):
        at = 42.0629 if phrase == 0 else nearest(requested)
        off(at)
        if phrase % 3 == 0:
            solid(at, WINDOWS[:6], RED if phrase % 2 == 0 else GOLD)
            solid(at, WINDOWS[6:10], GREEN)
            solid(at, WINDOWS[10:], GOLD if phrase % 2 == 0 else RED)
        elif phrase % 3 == 1:
            solid(at, WINDOWS[::2], GOLD)
            solid(at, WINDOWS[1::2], BLUE)
        else:
            solid(at, WINDOWS[:3] + WINDOWS[6:8] + WINDOWS[10:13], GREEN)
            solid(at, WINDOWS[3:6] + WINDOWS[8:10] + WINDOWS[13:], RED)
        solid(at, BUSHES[::2], GREEN if phrase % 2 else RED, 145)
        solid(at, BUSHES[1::2], RED if phrase % 2 else GREEN, 145)
        solid(at, [TREE], GOLD, 185)
        solid(at, [ICICLES], ICE, 130)

    # Each pane receives its own mapped rainbow, introduced in four waves.
    off(58.7233)
    solid(58.7233, WINDOWS, BLUE, 145)
    solid(58.7233, BUSHES + [TREE], GOLD, 115)
    solid(58.7233, [ICICLES], ICE, 100)
    for wave in range(4):
        cue(round(58.7233 + 1.5 * wave, 4), WINDOWS[wave::4],
            effect="Static rainbow", brightness=185)
    for at, effect in [(65.4733, "Static rainbow reversed"),
                       (68.4733, "Static rainbow"),
                       (71.4733, "Static rainbow reversed")]:
        cue(at, WINDOWS, effect=effect, brightness=195)

    # A complete outward and return sweep: 17 panes, then 16 back to the first.
    # The settled music is 160 BPM here: one selected logical zone per beat.
    window_route = WINDOW_ROUTE + WINDOW_ROUTE[-2::-1]
    for index, target in enumerate(window_route):
        solo(74.1181 + index * .375, target, GOLD, 200)
    # Keep each extra beat on one individual pane.
    for at, index in [(86.4931, 8), (87.2431, 0)]:
        solo(at, WINDOW_ROUTE[index], GOLD, 185)

    # Tree is one complete stop between the front bushes; the back-left
    # bush hands the spotlight to Lounge. Icicles follow Bar.
    for index, target in enumerate(GARDEN_ROUTE):
        solo(87.9572 + index * .375, target, GOLD, 205)
    # Complete one full loop, then gather the display for the native finale.
    off(97.3322)
    solid(97.3322, ALL, GOLD, 165)
    for at, brightness in [(98.4572, 185), (99.9572, 210), (101.4572, 230)]:
        solid(at, ALL, GOLD, brightness)

    cue(102.2026, [FRONT], stored_movie=True, movie_index=0, brightness=255)
    cue(119.0603, [FRONT], stored_movie=True, movie_index=1, brightness=255)

    chapters = [
        ("01_welcome", "Santa arrives", 0),
        ("02_bushes", "Bushes, one by one", 9.0093),
        ("03_windows", "Every window pane", 19.3654),
        ("04_tree", "Christmas tree", 32.299),
        ("05_icicles", "Winter icicles", 36.4553),
        ("06_colours", "Christmas colour arrangements", 42.0629),
        ("07_rainbows", "Rainbows in every pane", 58.7233),
        ("08_window_chase", "Round the windows and back", 74.1181),
        ("09_garden_loop", "Bushes to windows", 87.9572),
        ("10_movie_one", "Stored movie finale", 102.2026),
        ("11_movie_two", "Second stored movie", 119.0603),
        ("12_farewell", "Santa's farewell", 130),
    ]
    sections = []
    for index, (ident, title, start) in enumerate(chapters):
        end = chapters[index + 1][2] if index + 1 < len(chapters) else 140
        cues = []
        for event in sorted(events, key=lambda event: event["absolute"]):
            if start <= event["absolute"] < end:
                cues.append({"at": round(event["absolute"] - start, 4),
                             **{key: value for key, value in event.items() if key != "absolute"}})
        sections.append({"id": ident, "title": title, "start": start,
                         "duration": round(end - start, 4), "cues": cues})
    return {
        "schema_version": 2,
        "name": "Santa's Christmas Magic",
        "speaker": "media_player.sonos_move",
        "soundtrack": {"file": "christmas_show_v2.mp3", "duration": 140,
                       "title": "Santa's Christmas Magic", "version": 2},
        "requires_stored_movies": True,
        "voice": {"provider": "HeyGen", "name": "Santa - Public", "synthetic": True},
        "targets": ALL + [FRONT],
        "zones": {"bushes": BUSH_ROUTE, "windows": WINDOW_ROUTE,
                  "tree": [TREE], "icicles": [ICICLES]},
        "routes": {"window_chase": window_route,
                   "garden_loop": GARDEN_ROUTE},
        "music": {"title": "We Wish you a Merry Christmas", "artist": "Kevin MacLeod",
                  "source": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100369",
                  "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/"},
        "narration": narration,
        "sections": sections,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "custom_components/twinkly_area_state/christmas_show.json")
    args = parser.parse_args()
    plan = build()
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(plan['sections'])} chapters / 140 seconds to {args.output}")


if __name__ == "__main__":
    main()
