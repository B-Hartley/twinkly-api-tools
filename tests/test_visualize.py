"""Offline tests for plotting reader and group/name selection."""
import json
from pathlib import Path
import tempfile
import unittest

from visualize import load_snapshot, select_rows


class VisualizeTests(unittest.TestCase):
    def test_reads_wrapped_coordinates_and_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifest.json").write_text(json.dumps({"controllers": [
                {"name": "Back Bush Bottom", "ip": "192.168.1.86"}
            ]}), encoding="utf-8")
            folder = root / "controllers" / "192_168_1_86" / "responses"
            folder.mkdir(parents=True)
            (folder / "led_layout_full.json").write_text(json.dumps({
                "ok": True, "data": {"code": 1000, "coordinates": [
                    {"x": 0.25, "y": 0.75, "z": -1}, {"x": 0.5, "y": 0.9, "z": 0}
                ]}
            }), encoding="utf-8")
            (folder / "led_movie_config.json").write_text(json.dumps({
                "ok": True, "data": {"sync": {"mode": "master", "master_id": "ABCD-1234"}}
            }), encoding="utf-8")
            rows = load_snapshot(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["coordinates"], [(0.25, 0.75), (0.5, 0.9)])
            self.assertEqual(rows[0]["group"], "ABCD-1234")
            self.assertEqual(select_rows(rows, None, "abcd")[0]["name"], "Back Bush Bottom")
            self.assertEqual(select_rows(rows, ["Back Bush Bottom"], None)[0]["ip"], "192.168.1.86")
            with self.assertRaises(ValueError):
                select_rows(rows, ["Unknown set"], None)

    def test_missing_layout_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifest.json").write_text(json.dumps({"controllers": [
                {"name": "Offline", "ip": "192.168.1.7"}
            ]}), encoding="utf-8")
            self.assertEqual(load_snapshot(root), [])


if __name__ == "__main__":
    unittest.main()
