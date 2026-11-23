"""Offline guardrails for cloud layout PUT."""
import base64
import gzip
import json
import unittest
from unittest.mock import patch

import cloud_layout_update as clu


def make_fixture(source="3d"):
    points = [{"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 1.0, "y": 1.0, "z": 1.0}]
    cloud = {
        "id": 42, "name": "Office Left", "uuid": "u", "ledCount": 2,
        "devices": [{"part": 0, "device": {"name": "Office Left",
                                           "ledCount": 2, "password": "DO-NOT-LEAK"}}],
        "ledProfile": "p", "fps": 25, "capacity": 2, "meta": "{}",
        "palette": [], "layout": {
            "source": source, "geometry": "x", "name": "l",
            "synthesized": False, "uuid": "lu", "aspectXZ": 1, "aspectXY": 1,
            "coords": base64.b64encode(gzip.compress(json.dumps(points).encode())).decode(),
        },
    }
    member = {
        "name": "Office Left", "leds": 2, "standalone_layout": {
            "source": "3d", "synthesized": False,
            "coordinates": [{"x": 1, "y": 2, "z": 3}, {"x": 4, "y": 5, "z": 6}]},
    }
    return cloud, member


class CloudUpdateTests(unittest.TestCase):
    def test_construct_put_preserves_identity_and_device_fields(self):
        cloud, member = make_fixture()
        _, updated, before, target = clu.load_target([cloud], "Office Left", "42", member)
        self.assertEqual(updated["devices"], cloud["devices"])
        self.assertEqual(updated["uuid"], cloud["uuid"])
        self.assertEqual(updated["layout"]["uuid"], cloud["layout"]["uuid"])
        self.assertEqual(clu.decode_coords(updated["layout"]["coords"]), target)
        self.assertFalse(clu.coords_equal(before, target))

    def test_reject_source_change_to_3d(self):
        cloud, member = make_fixture("2d")
        with self.assertRaisesRegex(ValueError, "source/geometry"):
            clu.load_target([cloud], "Office Left", "42", member)

    def test_reject_wrong_object_identity(self):
        cloud, member = make_fixture()
        cloud["devices"][0]["device"]["name"] = "Different Device"
        with self.assertRaisesRegex(ValueError, "name"):
            clu.load_target([cloud], "Office Left", "42", member)

    def test_reject_area_object(self):
        cloud, member = make_fixture()
        cloud["devices"].append(cloud["devices"][0])
        with self.assertRaisesRegex(ValueError, "single-controller"):
            clu.load_target([cloud], "Office Left", "42", member)

    @patch("cloud_layout_update.urllib.request.build_opener")
    def test_put_rejects_non_numeric_id_without_network(self, opener):
        with self.assertRaises(ValueError):
            clu.put_object("../other", {}, "token", 10)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
