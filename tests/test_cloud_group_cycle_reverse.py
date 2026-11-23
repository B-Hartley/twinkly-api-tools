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
        s[preflight.FILES[0]]["count"] = len(s[preflight.FILES[0]]["objects"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            reverse.standalone_objects(s, plan())

    def test_live_reference_creation_is_immutable(self):
        s = standalone()
        with tempfile.TemporaryDirectory() as temp:
            import archive_3d_maps as refs
            for i, address in enumerate((LEFT, RIGHT)):
                refs.archive(temp, address.replace(":", ""), 2,
                             s[preflight.FILES[i + 1]]["layout"],
                             plan()["members"][i]["name"], "live-standalone")
            result = reverse.reference_check(s, plan(), temp, create=True)
            self.assertEqual(result, [LEFT.replace(":", ""), RIGHT.replace(":", "")])
            reverse.reference_check(s, plan(), temp, create=False)
            s[preflight.FILES[1]]["layout"]["coordinates"][0]["x"] += 0.5
            with self.assertRaises(ValueError):
                reverse.reference_check(s, plan(), temp, create=False)

    def test_no_reference_blocks_before_grouping(self):
        s = standalone()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Missing immutable 3D reference"):
                reverse.reference_check(s, plan(), tmp)

    def test_archive_is_authoritative_even_if_plan_is_stale(self):
        import archive_3d_maps as refs
        s = standalone()
        stale = copy.deepcopy(plan())
        stale["members"][0]["standalone_layout"]["coordinates"][0]["x"] += 5
        with tempfile.TemporaryDirectory() as tmp:
            for i, address in enumerate((LEFT, RIGHT)):
                refs.archive(tmp, address.replace(":", ""), 2,
                             s[preflight.FILES[i + 1]]["layout"],
                             stale["members"][i]["name"], "live-standalone")
            self.assertEqual(reverse.reference_check(s, stale, tmp),
                             [LEFT.replace(":", ""), RIGHT.replace(":", "")])
            maps = reverse.archived_layouts(
                stale, [LEFT.replace(":", ""), RIGHT.replace(":", "")], tmp)
            self.assertEqual(maps[0]["coordinates"],
                             s[preflight.FILES[1]]["layout"]["coordinates"])

    def test_group_cloud_detaches_two_standalones_before_create(self):
        originals = [{"id": 10}, {"id": 11}]
        with patch("cloud_group_cycle_reverse.cloud_fresh_standalones") as fresh, \
             patch("cloud_group_cycle_reverse.ops.cloud_write") as write:
            reverse.detach_original_standalones(originals, 6804, "token", 10)
        fresh.assert_called_once_with(originals, "token", 10)
        write.assert_called_once_with(
            "PUT", "/v3/installations/6804/objects",
            {"action": "remove", "objectIds": [10, 11]},
            "token", 200, 10)

    def test_group_cloud_refuses_duplicate_standalone_ids(self):
        with patch("cloud_group_cycle_reverse.ops.cloud_write") as write:
            with self.assertRaisesRegex(ValueError, "missing/duplicate"):
                reverse.detach_original_standalones(
                    [{"id": 10}, {"id": 10}], 6804, "token", 10)
            write.assert_not_called()

    def test_group_cloud_refuses_changed_cloud_inventory(self):
        with patch("cloud_group_cycle_reverse.cloud_fresh_standalones",
                   side_effect=RuntimeError("Cloud standalone disappeared")), \
             patch("cloud_group_cycle_reverse.ops.cloud_write") as write:
            with self.assertRaisesRegex(RuntimeError, "disappeared"):
                reverse.detach_original_standalones(
                    [{"id": 10}, {"id": 11}], 6804, "token", 10)
            write.assert_not_called()

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
            with self.assertRaisesRegex(ValueError, "No verified grouped cloud\\+LAN Area reference"):
                reverse.archived_group_template(
                    plan(), [LEFT.replace(":", ""), RIGHT.replace(":", "")], [tmp])


if __name__ == "__main__":
    unittest.main()
