"""Audience-level regression checks for the optional house-chase programme."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HouseChasePlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((ROOT / "custom_components/twinkly_area_state/christmas_show_house_chase.json").read_text())
        self.events = []
        start = 0
        for section in self.plan["sections"]:
            for cue in section["cues"]:
                self.events.append((round(start + cue["at"], 6), cue))
            start += section["duration"]
        self.duration = start

    def test_every_bush_and_every_individual_pane_has_a_solo_introduction(self):
        for zone, begin, end in [("bushes", 9, 19), ("windows", 19, 36)]:
            singles = [cue["targets"][0] for at, cue in self.events
                       if begin <= at < end and not cue.get("off") and len(cue["targets"]) == 1]
            self.assertEqual(set(singles), set(self.plan["zones"][zone]))
        self.assertEqual(len(self.plan["zones"]["bushes"]), 6)
        self.assertEqual(len(self.plan["zones"]["windows"]), 17)

    def test_all_nine_house_groups_have_a_short_full_brightness_chase(self):
        house = [(at, cue) for at, cue in self.events
                 if any(key.startswith("house:") for key in cue["targets"]) and not cue.get("off")]
        self.assertEqual(len(house), 9)
        self.assertEqual([cue["targets"][0] for _, cue in house], self.plan["routes"]["house_chase"])
        self.assertEqual([at for at, _ in house], list(range(52, 70, 2)))
        self.assertTrue(all(cue["brightness"] == 255 for _, cue in house))
        self.assertEqual(set(self.plan["house_rooms"]), set(self.plan["zones"]["house"]))

    def test_spoken_and_visible_countdown_immediately_precede_the_finale(self):
        counts = [(at, cue["effect"]) for at, cue in self.events if cue.get("effect", "").startswith("Countdown ")]
        self.assertEqual(counts, [(94, "Countdown 5"), (95, "Countdown 4"), (96, "Countdown 3"), (97, "Countdown 2"), (98, "Countdown 1")])
        narration = {line["at"]: line["text"] for line in self.plan["narration"]}
        self.assertEqual([narration[at].strip(".!") for at, _ in counts], ["Five", "Four", "Three", "Two", "One"])
        self.assertEqual(self.plan["finale"]["start"], counts[-1][0] + 1)

    def test_finale_uses_requested_names_in_order_at_five_second_intervals(self):
        movies = [(at, cue["effect"]) for at, cue in self.events if cue.get("effect", "").startswith("Movie: ")]
        names = ["Eclipse", "UpDown", "Bright Twinkle", "Vertical Flag", "Night Sky", "Christmas Spirit"]
        self.assertEqual(movies, [(99 + i * 5, "Movie: " + name) for i, name in enumerate(names)])
        self.assertTrue(self.plan["finale"]["keep_last_movie"])
        self.assertFalse(any(at >= movies[0][0] and not cue.get("effect", "").startswith("Movie: ") for at, cue in self.events))

    def test_no_undeclared_targets_or_overlapping_narration_slots(self):
        self.assertEqual(self.duration, self.plan["soundtrack"]["duration"])
        self.assertEqual(self.duration, 140)
        for at, cue in self.events:
            self.assertTrue(0 <= at < self.duration)
            self.assertLessEqual(set(cue["targets"]), set(self.plan["targets"]))
        previous_end = -1
        for line in self.plan["narration"]:
            self.assertGreaterEqual(line["at"], previous_end)
            self.assertLess(line["at"], line["until"])
            self.assertLessEqual(line["until"], self.duration)
            previous_end = line["until"]

    def test_checked_in_score_matches_offline_authoring_source(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            spec = importlib.util.spec_from_file_location("house_plan_authoring", ROOT / "tools/build_house_chase_plan.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.build(), self.plan)
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
