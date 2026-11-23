"""Offline tests of the immutable grouped 2D Area archive."""
import copy
import tempfile
import unittest
from pathlib import Path

import archive_2d_group as archive
import cloud_group_preflight as preflight
from test_cloud_group_preflight import stage


def grouped():
    s = stage()
    area = s[preflight.FILES[0]]["objects"][0]
    area.update({
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "capacity": 1024, "fps": 25, "ledProfile": "RGBW", "meta": "{}",
    })
    area["layout"].update({
        "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "geometry": "rectangle", "aspectXZ": 1.0, "aspectXY": 1.0,
        "name": "Area", "synthesized": False,
    })
    for i in range(2):
        s[preflight.FILES[i + 1]]["layout"]["synthesized"] = False
    return s


class ArchiveTests(unittest.TestCase):
    def test_creates_reloads_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = grouped()
            location = archive.archive(snapshot, root=tmp, source="test-snapshot")
            self.assertEqual(archive.read_archive(location)["manifest"]["led_count"], 4)
            self.assertEqual(archive.archive(snapshot, root=tmp), location)
            modified = copy.deepcopy(snapshot)
            modified[preflight.FILES[0]]["objects"][0]["name"] = "changed"
            with self.assertRaisesRegex(ValueError, "refusing overwrite"):
                archive.archive(modified, root=tmp)

    def test_corrupt_layout_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            location = archive.archive(grouped(), root=tmp)
            path = Path(location) / "office-left-layout-private.json"
            path.write_text('{"tampered":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                archive.read_archive(location)

    def test_cloud_uuid_must_match_local_group(self):
        bad = grouped()
        bad[preflight.FILES[0]]["objects"][0]["uuid"] = (
            "99999999-9999-4999-8999-999999999999")
        with self.assertRaisesRegex(ValueError, "UUID differs"):
            archive.verify(bad)

    def test_invalid_group_does_not_archive(self):
        bad = grouped()
        bad[preflight.FILES[2]]["group"]["group"]["mode"] = "none"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "not a fully verified"):
                archive.archive(bad, tmp)
            self.assertFalse(list(Path(tmp).rglob("manifest.json")))


if __name__ == "__main__":
    unittest.main()
