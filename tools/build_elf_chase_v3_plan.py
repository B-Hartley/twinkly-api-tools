#!/usr/bin/env python3
"""Build the separate V3 score without altering either existing programme.

The gold house lights are the elf. Ice-white frontage fairy lights follow the
same route four seconds per room. Two chases let the elf get two rooms ahead;
the middle chase gives him a whole three-room run before the pursuit resumes.
This is an offline authoring tool and cannot contact lights, Sonos or the car.
"""
import argparse
import json
from pathlib import Path

from build_christmas_plan import (
    ALL, BUSH_ROUTE, BUSHES, FRONT, GOLD, GREEN, ICE, ICICLES,
    PREFIX, RED, TREE, WINDOWS, WINDOW_ROUTE,
)
from build_house_chase_plan import HOUSE_ROOMS, MOVIES, PURSUIT_WINDOWS

ROOT = Path(__file__).resolve().parents[1]
LANDING = PREFIX + "landing"
DURATION = 212
HOUSE_ROUTE = list(HOUSE_ROOMS)
FAIRY_WINDOWS = dict(zip(HOUSE_ROUTE, PURSUIT_WINDOWS))
RUNS = [
    {"id": "two_rooms_ahead", "elf_start": 64, "fairy_start": 72,
     "rooms": ["house:front_door", "house:lounge", "house:hall", "house:downstairs_loo"]},
    {"id": "run_then_follow", "elf_start": 90, "fairy_start": 102,
     "rooms": ["house:bar", "house:office", "house:landing"]},
    {"id": "double_back", "elf_start": 114, "fairy_start": 122,
     "rooms": ["house:master_bedroom", "house:landing", "house:office"]},
]


def build():
    narration = json.loads((ROOT / "tools/elf_chase_v3_narration.json").read_text())
    events = []

    def cue(at, targets, **data):
        events.append({"absolute": round(at, 6), "targets": list(targets), **data})

    def off(at):
        cue(at, [FRONT], off=True)

    def solid(at, targets, rgb=GOLD, brightness=190):
        cue(at, targets, rgb=rgb, brightness=brightness)

    def solo(at, target, rgb=GOLD, brightness=190):
        off(at)
        solid(at, [target], rgb, brightness)

    def elf(at, room):
        cue(at, HOUSE_ROUTE, off=True)
        solid(at, [room], GOLD, 255)

    def fairies(at, room):
        off(at)
        solid(at, FAIRY_WINDOWS[room], ICE, 255)

    off(0)
    cue(0, HOUSE_ROUTE, off=True)
    palette = [RED, GREEN, GOLD, ICE, GREEN, GOLD]
    for i, target in enumerate(BUSH_ROUTE):
        solo(9 + i * 1.25, target, palette[i], 150)
    for i, target in enumerate(reversed(BUSH_ROUTE)):
        solo(16.5 + i * .38, target, GOLD, 180)
    for i, target in enumerate(WINDOW_ROUTE):
        solo(19 + i * .92, target, palette[i % len(palette)], 170)
    solo(36, TREE, GOLD, 220)
    solo(40, ICICLES, ICE, 210)
    off(45)
    solid(45, BUSHES[::2], RED, 180)
    solid(45, BUSHES[1::2], GREEN, 180)
    solid(45, [TREE], GOLD, 210)
    solid(45, [ICICLES], ICE, 170)
    for wave in range(4):
        cue(45 + wave * 1.5, WINDOWS[wave::4], effect="Static rainbow", brightness=210)

    # Drive first turns on during the headlights line, after the car setup.
    # Existing HouseLights wakes Jyggy at Prepare and flashes once here when
    # the wake command succeeded and house brightness is 100 percent.
    off(52)
    solid(52, [TREE], GOLD, 80)
    elf(58, "house:drive")
    for run in RUNS:
        for i, room in enumerate(run["rooms"]):
            elf(run["elf_start"] + i * 4, room)
            fairies(run["fairy_start"] + i * 4, room)
        # The elf slips out of sight before the pursuer reaches his last room.
        cue(run["elf_start"] + len(run["rooms"]) * 4, HOUSE_ROUTE, off=True)
        off(run["fairy_start"] + len(run["rooms"]) * 4)

    # A brief hiding glimpse, then an unhurried search and an unmistakable find.
    elf(134, "house:master_bedroom")
    cue(136, HOUSE_ROUTE, off=True)
    fairies(134, "house:office")
    fairies(138, "house:landing")
    elf(142, "house:master_bedroom")
    fairies(142, "house:master_bedroom")
    solid(146, ALL, GOLD, 220)
    cue(150, HOUSE_ROUTE, off=True)
    solid(150, ALL, GOLD, 240)

    off(156)
    for i, digit in enumerate((5, 4, 3, 2, 1)):
        cue(156 + i, [LANDING], effect=f"Countdown {digit}", brightness=255)
    for i, name in enumerate(MOVIES):
        cue(161 + i * 6, [FRONT], effect="Movie: " + name, brightness=255)
    # Refresh the car 30 seconds before Santa's farewell reminder. These
    # named drive-off cues leave the stored movie and other lights untouched.
    cue(174.4, ["house:drive"], off=True, car_wake="v3_farewell")
    cue(204.4, ["house:drive"], off=True, car_flash="v3_farewell")

    chapters = [
        ("welcome", "A little Christmas magic", 0),
        ("bushes", "Whole bushes, one by one", 9),
        ("windows", "Every individual window pane", 19),
        ("tree", "Christmas tree", 36),
        ("icicles", "Winter magic", 40),
        ("colours", "Christmas colours", 45),
        ("car", "The elf tries the car", 52),
        ("headlights", "The headlights spot him", 58),
        ("first_escape", "Two rooms ahead", 64),
        ("second_escape", "Run, then follow", 90),
        ("double_back", "The elf doubles back", 114),
        ("hide_and_seek", "Where is he hiding?", 134),
        ("found", "The fairy lights find the elf", 142),
        ("finale_setup", "The big finale", 150),
        ("countdown", "Count us in", 156),
        *[(f"finale_{i + 1}", f"Finale surprise {i + 1}", 161 + i * 6)
          for i in range(len(MOVIES))],
        ("farewell", "A Christmas farewell", 197),
    ]
    sections = []
    for i, (ident, title, start) in enumerate(chapters):
        end = chapters[i + 1][2] if i + 1 < len(chapters) else DURATION
        cues = [{"at": round(event["absolute"] - start, 6),
                 **{key: value for key, value in event.items() if key != "absolute"}}
                for event in sorted(events, key=lambda event: event["absolute"])
                if start <= event["absolute"] < end]
        sections.append({"id": ident, "title": title, "start": start,
                         "duration": end - start, "cues": cues})
    return {
        "schema_version": 2,
        "name": "The Elf, the Car and the Fairy Lights",
        "variant": "elf_chase_v3",
        "speaker": "media_player.sonos_move",
        "soundtrack": {"file": "christmas_show_elf_chase_v3.mp3", "duration": DURATION,
                       "title": "The Elf, the Car and the Fairy Lights", "version": 3,
                       "available": True},
        "requires_stored_movies": True,
        "required_movies": MOVIES,
        "voice": narration["voice"],
        "targets": ALL + [FRONT, LANDING] + HOUSE_ROUTE,
        "house_rooms": HOUSE_ROOMS,
        "zones": {"bushes": BUSH_ROUTE, "windows": WINDOW_ROUTE,
                  "tree": [TREE], "icicles": [ICICLES], "house": HOUSE_ROUTE},
        "routes": {"house_chase": HOUSE_ROUTE, "fairy_pursuit": FAIRY_WINDOWS},
        "chase": {"seconds_per_room": 4, "elf_colour": GOLD, "fairy_colour": ICE,
                  "runs": RUNS, "hide_at": 134, "find_at": 142},
        "car": {"wake": "during Prepare", "headlights_at": 58,
                "trigger": "first house:drive turn_on", "maximum_flashes": 2,
                "farewell": {"id": "v3_farewell", "wake_at": 174.4,
                             "headlights_at": 204.4, "requires_refresh_wake": True}},
        "countdown": {"start": 156, "digits": [5, 4, 3, 2, 1], "interval": 1,
                      "target": LANDING},
        "finale": {"start": 161, "movies": MOVIES, "seconds_per_movie": 6,
                   "keep_last_movie": True},
        "music": {"title": "We Wish you a Merry Christmas", "artist": "Kevin MacLeod",
                  "source": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100369",
                  "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                  "adaptation": "Extended with an instrumental chase repeat and crossfades"},
        "narration": narration["narration"],
        "sections": sections,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "custom_components/twinkly_area_state/christmas_show_elf_chase_v3.json")
    args = parser.parse_args()
    plan = build()
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(plan['sections'])} chapters / {DURATION} seconds to {args.output}")


if __name__ == "__main__":
    main()
