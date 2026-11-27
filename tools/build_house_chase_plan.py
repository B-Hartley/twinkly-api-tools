#!/usr/bin/env python3
"""Build the optional 140-second British Santa house-chase score, entirely offline.

The original show and soundtrack are never overwritten. Cue times are absolute
soundtrack seconds; the playback layer resolves room groups and movie names in
Prepare. The playful crash is an authored dim/black/recovery scene, not a real
controller failure or interruption of the soundtrack clock.
"""
import argparse
import json
from pathlib import Path

from build_christmas_plan import (
    ALL, BUSH_ROUTE, BUSHES, FRONT, GARDEN_ROUTE, GOLD, GREEN, ICE,
    ICICLES, PREFIX, RED, TREE, WINDOWS, WINDOW_ROUTE,
)

ROOT = Path(__file__).resolve().parents[1]
LANDING = PREFIX + "landing"
MOVIES = ["Eclipse", "UpDown", "Bright Twinkle", "Vertical Flag", "Night Sky", "Christmas Spirit"]
HOUSE_ROOMS = {
    "house:drive": "light.drive_lights",
    "house:front_door": "light.front_door_lights",
    "house:lounge": "light.lounge",
    "house:hall": ["light.hall", "light.hall_stairs_lightstrip"],
    "house:downstairs_loo": "light.downstairs_toilet",
    "house:bar": "light.bar",
    "house:office": "light.office",
    "house:landing": "light.landing",
    "house:master_bedroom": "light.master_bedroom",
}
HOUSE_ROUTE = list(HOUSE_ROOMS)
# The indoor elf is followed by a sparkle at the corresponding frontage.
PURSUIT_WINDOWS = [
    [BUSH_ROUTE[0], TREE], [ICICLES],
    WINDOWS[10:13], WINDOWS[8:10], [ICICLES], WINDOWS[13:17],
    WINDOWS[:6], WINDOWS[8:10], WINDOWS[6:8],
]


def build():
    narration = json.loads((ROOT / "tools/house_chase_narration.json").read_text())
    events = []

    def cue(at, targets, **data):
        events.append({"absolute": round(at, 6), "targets": list(targets), **data})

    def off(at):
        cue(at, [FRONT], off=True)

    def solid(at, targets, rgb=GOLD, brightness=190):
        cue(at, targets, brightness=brightness, rgb=rgb)

    def solo(at, target, rgb=GOLD, brightness=190):
        off(at)
        solid(at, [target], rgb, brightness)

    off(0)
    cue(0, HOUSE_ROUTE, off=True)
    palette = [RED, GREEN, GOLD, ICE, GREEN, GOLD]
    # Every bush is shown whole; the mapped Front Right halves are separate bushes.
    for i, target in enumerate(BUSH_ROUTE):
        solo(9 + i * 1.25, target, palette[i], 150)
    for i, target in enumerate(reversed(BUSH_ROUTE)):
        solo(16.5 + i * .38, target, GOLD, 180)
    # Retain an individual introduction for all seventeen window panes.
    for i, target in enumerate(WINDOW_ROUTE):
        solo(19 + i * .92, target, palette[i % len(palette)], 170)
    solo(36, TREE, GOLD, 200)
    solid(38, [TREE], GREEN, 220)
    solo(40, ICICLES, ICE, 180)
    solid(42, [ICICLES], GOLD, 200)
    off(44)
    solid(44, BUSHES[::2], RED, 180)
    solid(44, BUSHES[1::2], GREEN, 180)
    solid(44, [TREE], GOLD, 210)
    solid(44, [ICICLES], ICE, 170)
    for wave in range(4):
        cue(44 + wave * 1.5, WINDOWS[wave::4], effect="Static rainbow", brightness=210)

    # An eighteen-second chase: drive and door lead into full-output Hue rooms.
    # Each room holds for two seconds; coloured bulbs use gold and the runtime
    # keeps white-only bulbs bright white without sending unsupported RGB.
    off(52)
    solid(52, [TREE], GOLD, 110)
    for i, room in enumerate(HOUSE_ROUTE):
        at = 52 + i * 2
        cue(at, HOUSE_ROUTE, off=True)
        solid(at, [room], GOLD, 255)
        off(at + .9)
        solid(at + .9, PURSUIT_WINDOWS[i], ICE, 230)
    cue(70, HOUSE_ROUTE, off=True)
    # The sparkle escapes into the garden before the theatrical freeze.
    for i, target in enumerate(BUSH_ROUTE):
        solo(70 + i * .30, target, GOLD, 230)
    off(72)
    solid(72, ALL, ICE, 95)
    solid(74, ALL, ICE, 50)
    solid(76, ALL, ICE, 18)
    off(77.2)
    # Recovery follows the spoken pinch of magic and ends in a warm gold build.
    solo(80, TREE, GOLD, 50)
    for i, targets in enumerate([BUSHES, WINDOWS[10:17], WINDOWS[:6], WINDOWS[6:10], [ICICLES]]):
        solid(81 + i * .55, targets, GOLD, 125 + 20 * i)
    solid(84, ALL, GOLD, 200)
    solid(86, ALL, GOLD, 230)
    for i, target in enumerate(WINDOW_ROUTE):
        solo(89 + i * .25, target, GOLD, 210)
    # Exactly one spoken digit and one mapped Landing numeral each second.
    off(94)
    for i, digit in enumerate((5, 4, 3, 2, 1)):
        cue(94 + i, [LANDING], effect=f"Countdown {digit}", brightness=255)
    # Name-based selection is resolved before playback. Never substitute another
    # alphabetical movie when a requested name is missing or ambiguous.
    for i, name in enumerate(MOVIES):
        cue(99 + i * 5, [FRONT], effect="Movie: " + name, brightness=255)

    chapters = [
        ("welcome", "Father Christmas arrives", 0),
        ("bushes", "Whole bushes, one by one", 9),
        ("windows", "Every individual window pane", 19),
        ("tree", "Christmas tree", 36),
        ("icicles", "Winter icicles", 40),
        ("colours", "Christmas colours and rainbows", 44),
        ("house_chase", "An elf indoors, Twinkly in pursuit", 52),
        ("playful_crash", "A little snow in the works", 72),
        ("recovery", "Father Christmas restores the sparkle", 78),
        ("countdown_setup", "Count us in", 89),
        ("countdown", "Five, four, three, two, one", 94),
        *[("movie_" + str(i + 1), name, 99 + i * 5) for i, name in enumerate(MOVIES)],
        ("farewell", "A Christmas farewell", 129),
    ]
    sections = []
    for i, (ident, title, start) in enumerate(chapters):
        end = chapters[i + 1][2] if i + 1 < len(chapters) else 140
        cues = [{"at": round(event["absolute"] - start, 6),
                 **{key: value for key, value in event.items() if key != "absolute"}}
                for event in sorted(events, key=lambda e: e["absolute"])
                if start <= event["absolute"] < end]
        sections.append({"id": ident, "title": title, "start": start,
                         "duration": end - start, "cues": cues})
    return {
        "schema_version": 2,
        "name": "Father Christmas and the Cheeky Elf",
        "variant": "house_chase",
        "speaker": "media_player.sonos_move",
        "soundtrack": {"file": "christmas_show_house_chase_v1.mp3", "duration": 140,
                       "title": "Father Christmas and the Cheeky Elf", "version": 1},
        "requires_stored_movies": True,
        "required_movies": MOVIES,
        "voice": narration["voice"],
        "targets": ALL + [FRONT, LANDING] + HOUSE_ROUTE,
        "house_rooms": HOUSE_ROOMS,
        "zones": {"bushes": BUSH_ROUTE, "windows": WINDOW_ROUTE,
                  "tree": [TREE], "icicles": [ICICLES], "house": HOUSE_ROUTE},
        "routes": {"house_chase": HOUSE_ROUTE, "twinkly_pursuit": PURSUIT_WINDOWS},
        "countdown": {"start": 94, "digits": [5, 4, 3, 2, 1], "interval": 1,
                      "target": LANDING},
        "finale": {"start": 99, "movies": MOVIES, "seconds_per_movie": 5,
                   "keep_last_movie": True},
        "music": {"title": "We Wish you a Merry Christmas", "artist": "Kevin MacLeod",
                  "source": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100369",
                  "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/"},
        "narration": narration["narration"],
        "sections": sections,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "custom_components/twinkly_area_state/christmas_show_house_chase.json")
    args = parser.parse_args()
    plan = build()
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(plan['sections'])} chapters / 140 seconds to {args.output}")


if __name__ == "__main__":
    main()
