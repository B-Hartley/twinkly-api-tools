import json
from pathlib import Path
import unittest

class ChristmasChaseTests(unittest.TestCase):
    def test_chase_and_bush_targets(self):
        plan=json.loads((Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/christmas_show.json").read_text())
        diagnostics=next(s for s in plan["sections"] if s["id"]=="03_diagnostics")
        online=next(s for s in plan["sections"] if s["id"]=="04_system_online")
        front="light.twinkly_front"
        chase=[c for c in diagnostics["cues"] if c.get("effect")=="Window Chase"]
        self.assertEqual(len(chase),1)
        self.assertEqual(chase[0]["targets"],[front])
        self.assertEqual(diagnostics["duration"],15)
        self.assertEqual(online["cues"][0],{"at":0,"targets":[front],"off":True})
        for name in ("front_left_bush","back_right_bush","front_right_left_bush","front_right_right_bush"):
            self.assertIn("light.twinkly_"+name,plan["targets"])
        self.assertNotIn("light.twinkly_front_right_bush",plan["targets"])
        for section in plan["sections"]:
            for cue in section["cues"]:
                self.assertLess(cue["at"],section["duration"])
                self.assertTrue(set(cue["targets"])<=set(plan["targets"]))
                self.assertNotIn("light.twinkly_front_right_bush",cue["targets"])
        for name in ("front_right_left_bush","front_right_right_bush"):
            self.assertTrue(any(c["targets"]==["light.twinkly_"+name] for c in diagnostics["cues"]))
