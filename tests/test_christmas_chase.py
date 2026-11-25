import json
from pathlib import Path
import unittest

class ChristmasChaseTests(unittest.TestCase):
    def test_progressive_show(self):
        plan=json.loads((Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/christmas_show.json").read_text())
        sections={s["id"]:s for s in plan["sections"]}
        front="light.twinkly_front"
        targets=set(plan["targets"])-{front}
        active=set()
        seen=set()
        for section in plan["sections"][:3]:
            for cue in section["cues"]:
                if section["id"]=="03_diagnostics" and cue["at"]>=6.5:
                    break
                if cue.get("off"):
                    active.clear() if front in cue["targets"] else active.difference_update(cue["targets"])
                else:
                    active.update(cue["targets"])
                    seen.update(cue["targets"])
                    self.assertEqual(len(active),1)
                    self.assertIn("rgb",cue)
        self.assertEqual(seen,targets)
        demo=sections["05_demo_music"]["cues"]
        self.assertEqual(demo[-1]["effect"],"Window Chase")
        self.assertEqual(demo[-1]["targets"],[front])
        self.assertTrue(any(c.get("effect")=="Static rainbow" for c in demo[:-1]))
        finale=sections["11_grand_finale"]["cues"]
        self.assertTrue(all(c.get("stored_movie") and c["targets"]==[front] for c in finale))
        self.assertEqual(sections["12_startup_complete"]["cues"],[])
        for name in ("front_left_bush","back_right_bush","front_right_left_bush","front_right_right_bush"):
            self.assertIn("light.twinkly_"+name,targets)
        self.assertNotIn("light.twinkly_front_right_bush",targets)
        for section in plan["sections"]:
            for cue in section["cues"]:
                self.assertLess(cue["at"],section["duration"])
                self.assertTrue(set(cue["targets"])<=set(plan["targets"]))
                self.assertNotIn("light.twinkly_front_right_bush",cue["targets"])
