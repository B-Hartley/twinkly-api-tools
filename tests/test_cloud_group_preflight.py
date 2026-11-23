"""Offline regression tests for cloud/local preflight. No network required."""
import base64
import copy
import gzip
import json
import unittest

import cloud_group_preflight as preflight


LEFT = "aa:bb:cc:00:00:01"
RIGHT = "aa:bb:cc:00:00:02"


def encoded(points):
    return base64.b64encode(gzip.compress(json.dumps(points).encode())).decode()


def point(x):
    return {"x": float(x), "y": 0.0, "z": 0.0}


def local(mac, role, source, points, uid="group-uuid"):
    return {
        "gestalt": {"mac": mac, "number_of_led": len(points)},
        "group": {"group": {"mode": role, "uid": uid if role != "none" else None,
                            "size": 4 if role != "none" else 0,
                            "offset": 2 if role == "slave" else 0}},
        "layout": {"source": source, "coordinates": points}
    }


def object_record(oid, name, macs, source, points):
    return {
        "id": oid, "name": name, "ledCount": len(points),
        "devices": [{"part": index, "device": {"mac": mac}} for index, mac in enumerate(macs)],
        "layout": {"source": source, "coords": encoded(points)}
    }


def stage(grouped=True):
    a, b = [point(0), point(1)], [point(2), point(3)]
    if grouped:
        objects = [object_record(30, "Area", [LEFT, RIGHT], "2d", a + b)]
    else:
        objects = [object_record(11, "Left", [LEFT], "3d", a),
                   object_record(12, "Right", [RIGHT], "3d", b)]
    return {
        preflight.FILES[0]: {"count": len(objects), "next_page": None, "objects": objects},
        preflight.FILES[1]: local(LEFT, "master" if grouped else "none",
                                "2d" if grouped else "3d", a),
        preflight.FILES[2]: local(RIGHT, "slave" if grouped else "none",
                                "2d" if grouped else "3d", b)
    }


class PreflightTests(unittest.TestCase):
    def test_grouped_exact_match(self):
        result = preflight.evaluate(stage())
        self.assertEqual(result["result"], "PASS_READ_ONLY")
        self.assertEqual(result["state"], "grouped-cloud")
        self.assertEqual(result["mapping"]["mismatched_leds"], 0)

    def test_ungrouped_exact_match(self):
        result = preflight.evaluate(stage(False))
        self.assertEqual(result["result"], "PASS_READ_ONLY")
        self.assertEqual(result["state"], "ungrouped-cloud")

    def test_single_differing_coordinate_blocks(self):
        data = stage()
        obj = data[preflight.FILES[0]]["objects"][0]
        points = [point(0), point(1), point(2), point(3)]
        points[3]["x"] += 6.065918
        obj["layout"]["coords"] = encoded(points)
        result = preflight.evaluate(data)
        self.assertEqual(result["result"], "BLOCK")
        self.assertEqual(result["mapping"]["mismatched_leds"], 1)
        self.assertEqual(result["mapping"]["examples"][0]["index_zero_based"], 3)

    def test_diagnostics_show_roles_without_raw_group_uuid(self):
        data = stage()
        data[preflight.FILES[1]]["group"]["group"].update(
            {"mode": "none", "uid": None, "size": 0})
        report = preflight.evaluate(data)
        self.assertEqual(report["result"], "BLOCK")
        self.assertEqual(report["local_members"][0]["mode"], "none")
        self.assertEqual(report["local_members"][1]["mode"], "slave")
        self.assertFalse(report["local_uids_match"])
        self.assertNotIn("group-uuid", json.dumps(report))

    def test_partial_local_join_blocks(self):
        data = stage()
        data[preflight.FILES[1]]["group"]["group"]["mode"] = "none"
        self.assertEqual(preflight.evaluate(data)["result"], "BLOCK")

    def test_wrong_member_order_blocks(self):
        data = stage()
        data[preflight.FILES[0]]["objects"][0]["devices"].reverse()
        self.assertEqual(preflight.evaluate(data)["result"], "BLOCK")

    def test_duplicate_cloud_membership_blocks(self):
        data = stage()
        data[preflight.FILES[0]]["objects"].append(
            object_record(99, "Duplicate", [LEFT], "3d", [point(0), point(1)]))
        data[preflight.FILES[0]]["count"] = 2
        self.assertEqual(preflight.evaluate(data)["result"], "BLOCK")

    def test_count_mismatch_fails_closed(self):
        data = stage()
        data[preflight.FILES[0]]["count"] = 2
        with self.assertRaises(ValueError):
            preflight.evaluate(data)

    def test_missing_mapping_blocks_without_crash(self):
        data = stage()
        data[preflight.FILES[0]]["objects"][0]["layout"] = None
        result = preflight.evaluate(data)
        self.assertEqual(result["result"], "BLOCK")

    def test_unrelated_same_led_count_not_selected(self):
        data = stage(False)
        data[preflight.FILES[0]]["objects"].append(
            object_record(91, "Unrelated", ["11:22:33:44:55:66"], "3d", [point(5), point(6)]))
        data[preflight.FILES[0]]["count"] = 3
        self.assertEqual(preflight.evaluate(data)["result"], "PASS_READ_ONLY")
        self.assertEqual(len(preflight.evaluate(data)["matched_objects"]), 2)


if __name__ == "__main__":
    unittest.main()
