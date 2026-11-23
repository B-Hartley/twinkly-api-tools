"""Offline tests: supported Twinkly GET endpoints and immutable metadata archive."""
import json
import tempfile
import unittest
from pathlib import Path

import archive_effects
import twinkly_inspector as inspector


class EffectsCaptureTests(unittest.TestCase):
    def make_snapshot(self, root):
        root = Path(root) / "snapshot"
        controllers = []
        for i, ip in enumerate(("192.168.1.10", "192.168.1.11")):
            folder = root / "controllers" / ip.replace(".", "_") / "responses"
            folder.mkdir(parents=True)
            data = {
                "gestalt": {"mac": "aa:bb:cc:00:00:0" + str(i + 1),
                            "number_of_led": 210},
                "led_mode": {"mode": "movie"},
                "led_movie_config": {"frames_number": 20, "frame_delay": 83},
                "current_effect": {"effect_id": 2},
                "effects_catalogue": {"effects_number": 15},
                "movies_catalogue": {"movies": [
                    {"id": 2, "name": "RainbowSwing", "frames_number": 20}]},
                "movies_current": {"id": 2, "name": "RainbowSwing"},
                "playlist": {"entries": []},
                "playlist_current": {"id": 2},
            }
            for name, response in data.items():
                (folder / (name + ".json")).write_text(
                    json.dumps({"ok": True, "data": response}), encoding="utf-8")
            controllers.append({"ip": ip, "name": "Office " + str(i),
                                "status": "inspected"})
        (root / "manifest.json").write_text(json.dumps({
            "captured_utc": "2026-11-23T21:00:00+00:00",
            "controllers": controllers}), encoding="utf-8")
        return root

    def test_documented_get_paths(self):
        self.assertEqual(inspector.ENDPOINTS["movies_catalogue"], "/xled/v1/movies")
        self.assertEqual(inspector.ENDPOINTS["movies_current"],
                         "/xled/v1/led/movies/current")
        self.assertEqual(inspector.ENDPOINTS["playlist"], "/xled/v1/playlist")
        self.assertEqual(inspector.ENDPOINTS["effects_catalogue"],
                         "/xled/v1/led/effects")

    def test_archive_is_create_only_and_content_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make_snapshot(tmp)
            dest = archive_effects.archive(source, root=Path(tmp) / "archive")
            self.assertEqual(archive_effects.archive(source, root=Path(tmp) / "archive"), dest)
            saved = json.loads((dest / "reference.json").read_text(encoding="utf-8"))
            self.assertFalse(saved["limitations"]["raw_movie_frames_captured"])
            self.assertEqual(saved["controllers"][0]["responses"]["movies_catalogue"]
                             ["data"]["movies"][0]["name"], "RainbowSwing")
            (dest / "reference.json").write_text('{"tampered":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified"):
                archive_effects.archive(source, root=Path(tmp) / "archive")

    def test_missing_catalogue_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make_snapshot(tmp)
            target = next(source.rglob("movies_catalogue.json"))
            target.unlink()
            _, records = archive_effects.load_snapshot(source)
            self.assertFalse(records[0]["responses"]["movies_catalogue"]["ok"])

    def test_duplicate_hardware_identity_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make_snapshot(tmp)
            right = source / "controllers" / "192_168_1_11" / "responses" / "gestalt.json"
            response = json.loads(right.read_text(encoding="utf-8"))
            response["data"]["mac"] = "aa:bb:cc:00:00:01"
            right.write_text(json.dumps(response), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate controller MAC"):
                archive_effects.archive(source, root=Path(tmp) / "archive")


if __name__ == "__main__":
    unittest.main()
