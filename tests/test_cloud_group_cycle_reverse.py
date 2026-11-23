"""Offline safety tests of the standalone-first Twinkly cloud group cycle."""
import copy
import tempfile
import unittest
from unittest.mock import patch

import cloud_group_cycle as cycle
import cloud_group_cycle_reverse as reverse
import cloud_group_preflight as preflight
from test_cloud_group_preflight import stage, point, LEFT, RIGHT


def plan():
    return {"size": 4, "members": [
        {"name": "Office Left", "ip": "192.168.1.10",
         "leds": 2, "offset": 0, "standalone_layout": {
             "source": "3d", "synthesized": False, "coordinates": [point(0), point(1)]},
         "area_layout": {"source": "2d", "synthesized": False,
                         "coordinates": [point(0), point(1)]}},
        {"name": "Office Right", "ip": "192.168.1.11",
         "leds": 2, "offset": 2, "standalone_layout": {
             "source": "3d", "synthesized": False, "coordinates": [point(2), point(3)]},
         "area_layout": {"source": "2d", "synthesized": False,
                         "coordinates": [point(2), point(3)]}}
    ]}


def standalone():
    s = stage(False)
    for i in range(2):
        s[preflight.FILES[i + 1]]["layout"]["synthesized"] = False
    return s


class ReverseCycleTests(unittest.TestCase):
    def test_live_standalone_objects_match_mac_not_name(self):
        s = standalone()
        objects = reverse.standalone_objects(s, plan())
        self.assertEqual([o["id"] for o in objects], [11, 12])
        s[preflight.FILES[0]]["objects"][0]["name"] = "renamed"
        self.assertEqual(len(reverse.standalone_objects(s, plan())), 2)

    def test_wrong_cloud_mapping_blocks(self):
        s = standalone()
        s[preflight.FILES[0]]["objects"][0]["layout"]["source"] = "2d"
        with self.assertRaisesRegex(ValueError, "3D mapping unavailable"):
            reverse.standalone_objects(s, plan())

    def test_duplicate_cloud_identity_blocks(self):
        s = standalone()
        duplicate = copy.deepcopy(s[preflight.FILES[0]]["objects"][0])
        duplicate["id"] = 55
        s[preflight.FILES[0]]["objects"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            reverse.standalone_objects(s, plan())

    def test_live_reference_creation_is_immutable(self):
        s = standalone()
        with tempfile.TemporaryDirectory() as temp:
            result = reverse.reference_check(s, plan(), temp, create=True)
            self.assertEqual(result, [LEFT.replace(":", ""), RIGHT.replace(":", "")])
            reverse.reference_check(s, plan(), temp, create=False)
            s[preflight.FILES[1]]["layout"]["coordinates"][0]["x"] += 0.5
            with self.assertRaises(ValueError):
                reverse.reference_check(s, plan(), temp, create=False)

    def test_cloud_template_rejects_missing_device_fields(self):
        s = standalone()
        originals = reverse.standalone_objects(s, plan())
        template = {"layout": {"source": "2d"}}
        with self.assertRaisesRegex(ValueError, "create metadata"):
            reverse.validate_cloud_payload_inputs(template, originals, plan())

    def test_group_body_uuid_is_same_as_controller_uid(self):
        uid = "12345678-1234-1234-1234-123456789abc"
        group = {"name": "Office Group", "ledProfile": "RGBW",
                 "capacity": 1024, "fps": 25.0, "meta": {},
                 "layout": {"source": "2d", "coords": "",
                            "geometry": "rectangle", "uuid": "old",
                            "aspectXY": 1.0, "aspectXZ": 1.0,
                            "name": "Office", "synthesized": False}}
        standalones = [
            {"devices": [{"device": {"ledCount": 2, "mac": LEFT}}]},
            {"devices": [{"device": {"ledCount": 2, "mac": RIGHT}}]}
        ]
        with patch("cloud_group_cycle_reverse.ops.device_payload",
                   side_effect=lambda d, ip, timeout: d):
            body = reverse.new_area_body(group, standalones, plan(), 6804, uid, 10)
        self.assertEqual(body["uuid"], uid)
        self.assertEqual(body["ledCount"], 4)
        self.assertEqual(body["devices"][0]["part"], 0)
        self.assertEqual(body["devices"][1]["part"], 1)

    def test_missing_2d_template_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "No verified former"):
                reverse.archived_group_template(
                    plan(), [LEFT.replace(":", ""), RIGHT.replace(":", "")], [tmp])


if __name__ == "__main__":
    unittest.main()
