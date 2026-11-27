"""Audience-level checks for the separate slower elf-and-fairy-lights score."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ElfChaseV3PlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "custom_components/twinkly_area_state/christmas_show_elf_chase_v3.json").read_text())
        self.events = [(section["start"] + cue["at"], cue)
                       for section in self.plan["sections"] for cue in section["cues"]]

    def test_every_bush_and_pane_still_has_its_own_introduction(self):
        for zone, start, end in [("bushes", 9, 19), ("windows", 19, 36)]:
            singles = {cue["targets"][0] for at, cue in self.events
                       if start <= at < end and not cue.get("off") and len(cue["targets"]) == 1}
            self.assertEqual(singles, set(self.plan["zones"][zone]))
        self.assertEqual(len(self.plan["zones"]["windows"]), 17)

    def test_car_is_before_front_door_and_only_one_drive_flash_trigger(self):
        drive = [at for at, cue in self.events if "house:drive" in cue["targets"] and not cue.get("off")]
        door = [at for at, cue in self.events if "house:front_door" in cue["targets"] and not cue.get("off")]
        self.assertEqual(drive, [58])
        self.assertEqual(door, [64])
        self.assertLess(drive[0], door[0])

    def test_gold_elf_has_four_seconds_in_each_room_and_pursuit_varies(self):
        gold = self.plan["chase"]["elf_colour"]
        ice = self.plan["chase"]["fairy_colour"]
        self.assertNotEqual(gold, ice)
        for run in self.plan["chase"]["runs"]:
            for i, room in enumerate(run["rooms"]):
                elf_at = run["elf_start"] + i * 4
                fairy_at = run["fairy_start"] + i * 4
                self.assertTrue(any(at == elf_at and cue["targets"] == [room]
                                    and cue.get("rgb") == gold and cue.get("brightness") == 255
                                    for at, cue in self.events))
                arrivals = [cue for at, cue in self.events if at == fairy_at and cue.get("rgb") == ice]
                self.assertTrue(any(cue["targets"] == self.plan["routes"]["fairy_pursuit"][room]
                                    for cue in arrivals))
            self.assertGreaterEqual(run["fairy_start"] - run["elf_start"], 8)
        self.assertEqual([run["fairy_start"] - run["elf_start"] for run in self.plan["chase"]["runs"]], [8, 12, 8])

    def test_all_house_rooms_are_used_and_the_find_has_both_characters(self):
        lit_rooms = {target for _, cue in self.events if not cue.get("off")
                     for target in cue["targets"] if target.startswith("house:")}
        self.assertEqual(lit_rooms, set(self.plan["house_rooms"]))
        find = [(at, cue) for at, cue in self.events if at == self.plan["chase"]["find_at"]]
        self.assertTrue(any(cue.get("rgb") == self.plan["chase"]["elf_colour"] for _, cue in find))
        self.assertTrue(any(cue.get("rgb") == self.plan["chase"]["fairy_colour"] for _, cue in find))

    def test_spoken_words_use_fairy_lights_and_finale_reactions(self):
        text = " ".join(line["text"] for line in self.plan["narration"]).lower()
        self.assertIn("fairy lights", text)
        self.assertIn("headlights", text)
        self.assertIn("big finale", text)
        self.assertNotIn("twinkly", text)
        for name in self.plan["required_movies"]:
            self.assertNotIn(name.lower(), text)

    def test_countdown_matches_voice_and_six_movies_have_more_viewing_time(self):
        counts = [(at, cue["effect"]) for at, cue in self.events if cue.get("effect", "").startswith("Countdown ")]
        self.assertEqual(counts, [(156 + i, f"Countdown {5 - i}") for i in range(5)])
        spoken = {line["at"]: line["text"].strip(".!") for line in self.plan["narration"]}
        self.assertEqual([spoken[at] for at, _ in counts], ["Five", "Four", "Three", "Two", "One"])
        movies = [(at, cue["effect"]) for at, cue in self.events if cue.get("effect", "").startswith("Movie: ")]
        self.assertEqual(movies, [(161 + i * 6, "Movie: " + name) for i, name in enumerate(self.plan["required_movies"])])
        self.assertTrue(self.plan["finale"]["keep_last_movie"])
        self.assertFalse(any(at >= 161 and not cue.get("effect", "").startswith("Movie: ") for at, cue in self.events))

    def test_timing_is_complete_and_narration_slots_do_not_overlap(self):
        self.assertEqual(sum(section["duration"] for section in self.plan["sections"]), 212)
        self.assertEqual(self.plan["soundtrack"]["duration"], 212)
        end = 0
        for line in self.plan["narration"]:
            self.assertGreaterEqual(line["at"], end)
            self.assertLess(line["at"], line["until"])
            self.assertLess(line["until"], 212)
            end = line["until"]
        for at, cue in self.events:
            self.assertTrue(0 <= at < 212)
            self.assertLessEqual(set(cue["targets"]), set(self.plan["targets"]))

    def test_checked_in_score_matches_authoring_source(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            spec = importlib.util.spec_from_file_location("v3_plan_authoring", ROOT / "tools/build_elf_chase_v3_plan.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.build(), self.plan)
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
