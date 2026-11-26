"""Offline coverage of digit geometry and standalone timing score."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state"
spec=importlib.util.spec_from_file_location("countdown",ROOT/"countdown.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CountdownTests(unittest.TestCase):
    def layout(self):
        return {"source":"2d","coordinates":[{"x":i/9,"y":j/20} for i in range(10) for j in range(21)]}

    def test_distinct_frames_and_wire_order(self):
        frames=module.countdown_patterns(self.layout(),210,4)
        self.assertEqual(set(frames),{f"Countdown {d}" for d in range(1,6)})
        self.assertEqual(len(set(frames.values())),5)
        for frame in frames.values():
            self.assertEqual(len(frame),840)
            self.assertEqual(set(frame[0::4]),{0})
            self.assertEqual(set(frame[1::4]),{0,255})
        layout=self.layout()
        layout["coordinates"].reverse()
        reversed_frames=module.countdown_patterns(layout,210,4)
        for name,frame in frames.items():
            self.assertEqual(reversed_frames[name],b"".join(frame[i:i+4] for i in range(836,-1,-4)))

    def test_invalid_map(self):
        for bad in ({}, {"source":"3d","coordinates":self.layout()["coordinates"]},
                    {"source":"2d","coordinates":[{"x":1,"y":1}]*210}):
            with self.assertRaises(ValueError):
                module.countdown_patterns(bad,210,4)

    def test_independent_score(self):
        plan=json.loads((ROOT/"countdown_test.json").read_text())
        self.assertFalse(plan["requires_stored_movies"])
        self.assertEqual(plan["soundtrack"]["duration"],10)
        cues=plan["sections"][0]["cues"]
        self.assertEqual([cue["at"] for cue in cues],[0,3,4,5,6,7,8])
        self.assertTrue(cues[0]["off"] and cues[-1]["off"])
        self.assertEqual([cue["effect"] for cue in cues[1:-1]],[f"Countdown {d}" for d in (5,4,3,2,1)])
        self.assertTrue(all(cue["targets"]==["light.twinkly_landing"] for cue in cues[1:-1]))
        self.assertNotEqual(plan["soundtrack"]["file"],"christmas_show_v2.mp3")
