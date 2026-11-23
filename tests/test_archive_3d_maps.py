"""Offline tests of immutable Twinkly 3D reference maps."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
import archive_3d_maps as archive

MAC = "aabbcc000001"


def mapping():
    return {"source": "3d", "synthesized": False,
            "coordinates": [{"x": 1.0, "y": 2.0, "z": 3.0},
                            {"x": 4.0, "y": 5.0, "z": 6.0}]}


def plan():
    return {"members": [{"name": "Office Left", "ip": "192.168.1.10",
                         "leds": 2, "standalone_layout": mapping()}]}


class ArchiveTests(unittest.TestCase):
    def test_immutable_and_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(archive.archive(tmp, MAC, 2, mapping(), "Office Left",
                                             "saved-plan"), "created")
            self.assertEqual(archive.archive(tmp, MAC, 2, mapping(), "Office Left",
                                             "saved-plan"), "existing")
            changed = copy.deepcopy(mapping())
            changed["coordinates"][0]["x"] = 999
            with self.assertRaisesRegex(ValueError, "DIFFERENT"):
                archive.archive(tmp, MAC, 2, changed, "Office Left", "saved-plan")
            self.assertEqual(archive.read_reference(tmp, MAC)["layout"], mapping())

    def test_checksum_detects_manual_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive.archive(tmp, MAC, 2, mapping(), "Office Left", "saved-plan")
            path = archive.reference_path(tmp, MAC)
            data = json.loads(path.read_text())
            data["layout"]["coordinates"][0]["x"] = 999
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "checksum"):
                archive.read_reference(tmp, MAC)

    def test_never_convert_2d_to_3d(self):
        wrong = mapping()
        wrong["source"] = "2d"
        with self.assertRaises(ValueError):
            archive.verify_layout(wrong, 2)

    @patch("archive_3d_maps.read_local")
    @patch("archive_3d_maps.load_plan")
    def test_office_plan_requires_opt_in_when_grouped(self, load, read):
        load.return_value = (plan(), "uid")
        read.return_value = {
            "gestalt": {"mac": "aa:bb:cc:00:00:01", "number_of_led": 2},
            "layout": {"source": "2d", "coordinates": mapping()["coordinates"]}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "allow-saved-plan"):
                archive.from_plan("ignored.json", tmp)
            self.assertEqual(archive.from_plan("ignored.json", tmp, True), 1)
            ref = archive.read_reference(tmp, MAC)
            self.assertEqual(ref["provenance"],
                             "saved-private-plan-3d-not-current-live-map")

    def test_select_only_named_devices_and_deduplicate(self):
        devices = [{"name": "Light Tree", "ip": "192.168.1.20"},
                   {"name": "Mid Right", "ip": "192.168.1.21"},
                   {"name": "Icicles", "ip": "192.168.1.22"}]
        with patch("archive_3d_maps.get_devices", return_value=devices), \
             patch("archive_3d_maps.read_device", return_value=(MAC, 2, mapping())), \
             patch("archive_3d_maps.archive", return_value="created") as write:
            created, skipped = archive.from_devices(
                "devices.json", "/ignored", names=["LIGHT TREE", "Mid Right", "mid right"])
        self.assertEqual((created, skipped), (2, 0))
        self.assertEqual(write.call_count, 2)

    def test_unknown_name_errors_not_silently_skipped(self):
        with patch("archive_3d_maps.get_devices", return_value=[]):
            with self.assertRaisesRegex(ValueError, "Not found"):
                archive.from_devices("devices.json", "/ignored", names=["BackL Bush"])

    def test_nonfinite_rejected(self):
        wrong = mapping()
        wrong["coordinates"][0]["x"] = float("nan")
        with self.assertRaises(ValueError):
            archive.verify_layout(wrong, 2)


if __name__ == "__main__":
    unittest.main()
