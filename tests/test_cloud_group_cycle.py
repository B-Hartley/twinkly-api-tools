"""Offline tests for the experimental cloud+LAN round trip. No network calls."""
import argparse
import base64
import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_group_cycle as cycle
import cloud_group_preflight as preflight
from test_cloud_group_preflight import stage, point


class CycleTests(unittest.TestCase):
    def test_create_body_matches_captured_keys(self):
        device = {"ledCount": 2}
        layout = {"source": "3d", "coords": cycle.encoded_layout([point(1), point(2)]),
                  "geometry": "undefined", "uuid": "template", "aspectXZ": 1.0,
                  "aspectXY": 2.0, "name": "Office Left", "synthesized": False}
        result = cycle.create_body(
            {"ledProfile": "RGBW", "capacity": 1024, "fps": 25.0},
            [{"part": 0, "device": device}], layout,
            name="Office Left", installation_id=4321, metadata="{}")
        self.assertEqual(set(result), {
            "name", "installationId", "ledProfile", "capacity",
            "devices", "fps", "meta", "layout", "uuid", "ledCount"})
        self.assertEqual(result["ledCount"], 2)
        self.assertNotEqual(result["uuid"], layout["uuid"])
        self.assertEqual(result["layout"]["source"], "3d")

    def test_group_cloud_uuid_must_equal_local_group_uuid(self):
        group_uid = "12345678-1234-1234-1234-123456789abc"
        layout = {"source": "2d", "coords": cycle.encoded_layout([point(1), point(2)]),
                  "geometry": "undefined", "uuid": "old", "aspectXZ": 1.0,
                  "aspectXY": 2.0, "name": "Office Group", "synthesized": False}
        result = cycle.create_body(
            {"ledProfile": "RGBW", "capacity": 1024, "fps": 25.0},
            [{"part": 0, "device": {"ledCount": 1}},
             {"part": 1, "device": {"ledCount": 1}}],
            layout, name="Office Group", installation_id=4321, metadata="{}",
            object_uuid=group_uid)
        self.assertEqual(result["uuid"], group_uid)
        with self.assertRaises(ValueError):
            cycle.create_body(
                {"ledProfile": "RGBW", "capacity": 1024, "fps": 25.0},
                [{"part": 0, "device": {"ledCount": 1}}], layout,
                name="Office Group", installation_id=4321, metadata="{}",
                object_uuid="not-a-uuid")

    def test_cloud_area_uuid_must_match_group_uid(self):
        sample = stage()
        group_obj = sample[preflight.FILES[0]]["objects"][0]
        group_obj["uuid"] = "12345678-1234-1234-1234-123456789abc"
        from test_cloud_group_preflight import LEFT, RIGHT
        plan = {"size": 4, "members": [{"name": "Office Left"},
                                       {"name": "Office Right"}]}
        match = cycle.find_area(sample, plan)
        self.assertEqual(match["id"], group_obj["id"])
        group_obj["uuid"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        with self.assertRaisesRegex(ValueError, "UUID differs"):
            cycle.find_area(sample, plan)

    @patch("cloud_group_cycle.urllib.request.build_opener")
    def test_cloud_write_only_approved_paths(self, opener):
        with self.assertRaises(ValueError):
            cycle.cloud_write("DELETE", "/v3/objects/123", {}, "token", 200, 10)
        with self.assertRaises(ValueError):
            cycle.cloud_write("PUT", "/v3/installations/123/objects",
                              {"action": "add", "objectIds": [42]}, "token", 200, 10)
        with self.assertRaises(ValueError):
            cycle.cloud_write("POST", "/v3/objects/123", {}, "token", 200, 10)
        opener.assert_not_called()

    def test_cloud_roundtrip_precision_matches_preflight_tolerance(self):
        original = [point(1), point(2)]
        latest = copy.deepcopy(original)
        latest[0]["x"] += 0.00024405
        self.assertFalse(preflight.compare_points(original, latest)["matching"])
        self.assertTrue(preflight.compare_points(
            original, latest, tolerance=cycle.CLOUD_LOCAL_TOLERANCE)["matching"])
        latest[0]["x"] += 0.00010
        comparison = preflight.compare_points(
            original, latest, tolerance=cycle.CLOUD_LOCAL_TOLERANCE)
        self.assertFalse(comparison["matching"])
        self.assertEqual(comparison["mismatched_leds"], 1)

    def test_default_dry_run_does_not_write(self):
        args = argparse.Namespace(
            plan="private-area-plan.json", group="Office Windows", timeout=10,
            execute=False, acknowledge_risk=False, confirm_group=None,
            output="cloud-preflight-backups")
        plan = {"members": [
            {"name": "Office Left", "leds": 2},
            {"name": "Office Right", "leds": 2}]}
        snapshot = stage()
        cloud = snapshot[preflight.FILES[0]]["objects"][0]
        cloud["installation"] = {"id": 4321}
        report = preflight.evaluate(snapshot)
        report["result"] = "PASS_READ_ONLY"
        with (
            patch("cloud_group_cycle.load_plan", return_value=(plan, "uid")),
            patch("cloud_group_cycle.checkpoint", return_value=(plan, snapshot)),
            patch("cloud_group_cycle.find_area", return_value=cloud),
            patch("cloud_group_cycle.verified_grouped_plan", return_value=plan),
            patch("cloud_group_cycle.find_standalone_templates",
                  return_value={"Office Left": {}, "Office Right": {}}),
            patch("cloud_group_cycle.cloud_write") as write,
            patch("cloud_group_cycle.local_write") as local,
        ):
            self.assertEqual(cycle.run(args), 0)
            write.assert_not_called()
            local.assert_not_called()

    def test_missing_template_blocks(self):
        plan = {"members": [
            {"name": "Office Left", "leds": 2,
             "standalone_layout": {"coordinates": [point(0), point(1)]}},
            {"name": "Office Right", "leds": 2,
             "standalone_layout": {"coordinates": [point(2), point(3)]}}]}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "No verified private standalone"):
                cycle.find_standalone_templates(
                    plan, ["aabbcc000001", "aabbcc000002"], (temp,))

    def test_checkpoint_requires_expected_state(self):
        good = stage()
        report = preflight.evaluate(good)
        with patch("cloud_group_cycle.collect",
                   return_value=(report, {"members": [1, 2]}, good)):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                cycle.checkpoint("3 CHECK UNGROUPED", "private.json",
                                 "ungrouped-cloud", "output", 10)

    @patch("cloud_group_cycle.request")
    def test_local_failure_not_silently_accepted(self, request):
        request.return_value = {"ok": False, "status": 401, "error": "HTTP 401"}
        with patch("cloud_group_cycle.authenticate", return_value=("token", {})):
            with self.assertRaisesRegex(RuntimeError, "ambiguous or failed"):
                cycle.local_write({"name": "Office Left", "ip": "192.168.1.10"},
                                  cycle.GROUP_API, {"group": {"mode": "master"}}, 10)
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
