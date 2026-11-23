"""Safety and state transition tests; no network or devices required."""
import json
from pathlib import Path
import tempfile
import unittest

from state_manager import analyse, read_state, resolve


class StateManagerTests(unittest.TestCase):
    def make_state(self, root, name, grouped):
        folder = root / name / "20261123T120000Z"
        responses = folder / "controllers" / "192_168_1_10" / "responses"
        responses.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({
            "state_name": name, "controllers": [{
                "ip": "192.168.1.10", "name": "Front Bush", "status": "inspected",
                "identity": {"number_of_led": 400, "mac": "aa:bb:cc:dd:ee:ff"},
                "endpoints": {"led_layout_full": {"ok": True, "sha256": "layout-hash"}}
            }]
        }), encoding="utf-8")
        sync = {"mode": "master", "master_id": "group-uuid"} if grouped else {"mode": "none"}
        (responses / "led_movie_config.json").write_text(json.dumps({
            "ok": True, "data": {"sync": sync}
        }), encoding="utf-8")
        return folder

    def test_grouped_to_ungrouped_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_state(root, "frontgroup", True)
            self.make_state(root, "ungrouped", False)
            old = read_state(resolve(root, "frontgroup"))
            new = read_state(resolve(root, "ungrouped"))
            plan = analyse(old, new, "group-")
            self.assertEqual(plan["master"], "Front Bush")
            self.assertFalse(plan["restorable_automatically"])
            self.assertEqual(plan["issues"], [])
            self.assertTrue(plan["controllers"][0]["same_layout"])

    def test_still_grouped_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_state(root, "frontgroup", True)
            self.make_state(root, "ungrouped", True)
            plan = analyse(read_state(resolve(root, "frontgroup")),
                           read_state(resolve(root, "ungrouped")), "group-")
            self.assertTrue(any("still report group" in p for p in plan["issues"]))

    def test_no_missing_or_ambiguous_group_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_state(root, "frontgroup", False)
            self.make_state(root, "ungrouped", False)
            with self.assertRaises(ValueError):
                analyse(read_state(resolve(root, "frontgroup")),
                        read_state(resolve(root, "ungrouped")))


if __name__ == "__main__":
    unittest.main()
