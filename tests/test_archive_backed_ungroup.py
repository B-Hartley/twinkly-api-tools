"""Regression coverage for ungroup's immutable-reference preflight."""
import unittest
from unittest.mock import patch
import cloud_group_cycle as cycle


def layout(source, x):
    return {"source": source, "synthesized": False,
            "coordinates": [{"x": x, "y": 0.0, "z": 0.0}]}


def fixture():
    group = {"id": 11, "name": "Office Group", "uuid": "56cd9f01-480e-4ab9-b9d3-cd4c7d8027d8",
             "ledCount": 2, "layout": layout("2d", 0)}
    plan = {"size": 2, "members": [
        {"name": "Left", "leds": 1, "standalone_layout": layout("3d", 99)},
        {"name": "Right", "leds": 1, "standalone_layout": layout("3d", 99)}]}
    snapshot = {cycle.FILES[1]: {"gestalt": {"mac": "aa:bb:cc:dd:ee:01",
                                           "number_of_led": 1}, "layout": layout("2d", 0)},
                cycle.FILES[2]: {"gestalt": {"mac": "aa:bb:cc:dd:ee:02",
                                           "number_of_led": 1}, "layout": layout("2d", 1)}}
    ref = {"layout": layout("3d", 0.3), "led_count": 1,
           "provenance": "captured-live-standalone-3d"}
    return plan, snapshot, group, ref


class ArchiveBackedPreflightTests(unittest.TestCase):
    @patch("cloud_group_cycle.find_area")
    @patch("archive_2d_group.read_archive")
    @patch("archive_3d_maps.read_reference")
    @patch("pathlib.Path.rglob")
    @patch("pathlib.Path.is_dir", return_value=True)
    def test_stale_plan_3d_is_replaced_by_immutable_reference(
            self, isdir, rglob, read3d, read2d, find):
        from pathlib import Path
        from unittest.mock import MagicMock
        plan, snapshot, obj, reference = fixture()
        # Cloud point geometry is patched to return the expected grouped pairing.
        obj["layout"]["coordinates"] = layout("2d", 0)["coordinates"] + layout("2d", 1)["coordinates"]
        find.return_value = obj
        read3d.return_value = reference
        read2d.return_value = {
            "manifest": {"macs_in_order": ["aabbccddee01", "aabbccddee02"], "led_count": 2},
            "area": obj,
            "layouts": [layout("2d", 0), layout("2d", 1)]
        }
        manifest = MagicMock()
        manifest.parent = Path("2d_group_archive/test")
        manifest.stat.return_value.st_mtime = 1
        rglob.return_value = [manifest]
        with patch.object(cycle, "coordinates", side_effect=lambda item: item["coordinates"]):
            verified = cycle.verified_grouped_plan(plan, snapshot)
        self.assertEqual(verified["members"][0]["standalone_layout"], reference["layout"])
        self.assertNotEqual(verified["members"][0]["standalone_layout"],
                            plan["members"][0]["standalone_layout"])
        self.assertEqual(verified["members"][1]["area_layout"], layout("2d", 1))

    @patch("cloud_group_cycle.find_area")
    @patch("archive_2d_group.read_archive")
    @patch("pathlib.Path.rglob")
    @patch("pathlib.Path.is_dir", return_value=True)
    def test_different_group_map_blocks_before_3d_use(
            self, isdir, rglob, read2d, find):
        from pathlib import Path
        from unittest.mock import MagicMock
        plan, snapshot, obj, reference = fixture()
        obj["layout"]["coordinates"] = layout("2d", 0)["coordinates"] + layout("2d", 1)["coordinates"]
        find.return_value = obj
        different = dict(obj)
        different["layout"] = {"coordinates": layout("2d", -4)["coordinates"] + layout("2d", 1)["coordinates"]}
        read2d.return_value = {
            "manifest": {"macs_in_order": ["aabbccddee01", "aabbccddee02"], "led_count": 2},
            "area": different, "layouts": [layout("2d", -4), layout("2d", 1)]}
        manifest = MagicMock()
        manifest.parent = Path("2d_group_archive/test")
        rglob.return_value = [manifest]
        with patch.object(cycle, "coordinates", side_effect=lambda item: item["coordinates"]):
            with self.assertRaisesRegex(ValueError, "No matching immutable 2D"):
                cycle.verified_grouped_plan(plan, snapshot)


if __name__ == "__main__":
    unittest.main()
