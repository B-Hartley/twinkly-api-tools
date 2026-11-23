"""Mocked live preflight collection tests. All networking and secrets are fake."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_group_live as live
import cloud_group_preflight as checker
from test_cloud_group_preflight import stage


def sample_plan():
    return {
        "schema": 1, "group_name": "Office Windows", "size": 4,
        "members": [
            {"name": "Office Left", "ip": "192.168.1.10", "leds": 2, "offset": 0},
            {"name": "Office Right", "ip": "192.168.1.11", "leds": 2, "offset": 2},
        ],
    }


class LivePreflightTests(unittest.TestCase):
    @patch("cloud_group_live.evaluate")
    @patch("cloud_group_live.read_local")
    @patch("cloud_group_live.cloud_get")
    @patch("cloud_group_live.get_cloud_token", return_value="fake")
    @patch("cloud_group_live.load_plan")
    def test_live_collect_reads_exactly_two_cloud_gets_and_backs_up(
            self, load_plan, token, cloud_get, read_local, evaluate):
        plan = sample_plan()
        load_plan.return_value = (plan, "group-uuid")
        snapshot = stage()
        cloud_get.side_effect = [snapshot[checker.FILES[0]], {"installations": []}]
        read_local.side_effect = [snapshot[checker.FILES[1]],
                                  snapshot[checker.FILES[2]]]
        evaluate.return_value = {"result": "PASS_READ_ONLY", "matched_objects": [],
                                 "issues": [], "mapping": None}
        with tempfile.TemporaryDirectory() as tmp:
            result, parsed = live.collect("private-plan.json", output=tmp)
            self.assertEqual(result["result"], "PASS_READ_ONLY")
            self.assertEqual(parsed, plan)
            files = list(Path(tmp).glob("*/cloud-objects-PRIVATE.json"))
            self.assertEqual(len(files), 1)
            self.assertTrue(list(Path(tmp).glob("*/cloud-installations-PRIVATE.json")))
        self.assertEqual(cloud_get.call_count, 2)
        self.assertEqual(read_local.call_count, 2)
        self.assertEqual(cloud_get.call_args_list[0].args[0],
                         "/v3/objects?mine=true&fields=layout,devices,capabilities")

    @patch("cloud_group_live.read_local", side_effect=RuntimeError("Controller offline"))
    @patch("cloud_group_live.cloud_get")
    @patch("cloud_group_live.get_cloud_token", return_value="fake")
    @patch("cloud_group_live.load_plan")
    def test_offline_controller_blocks(self, load_plan, token, cloud_get, read_local):
        load_plan.return_value = (sample_plan(), "group-uuid")
        cloud_get.side_effect = [stage()[checker.FILES[0]], {}]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "Controller offline"):
                live.collect("p.json", output=tmp)
            self.assertEqual(list(Path(tmp).glob("*")), [])

    @patch("cloud_group_live.load_plan")
    def test_more_than_two_members_not_supported(self, load_plan):
        plan = sample_plan()
        plan["members"].append({"name": "Third", "ip": "192.168.1.12",
                                "leds": 2, "offset": 4})
        load_plan.return_value = (plan, "group-uuid")
        with self.assertRaisesRegex(ValueError, "exactly two"):
            live.collect("plan.json")

    @patch("cloud_group_live.lan_authenticate", return_value=(None, {"login": "failed"}))
    @patch("cloud_group_live.request")
    def test_lan_auth_failure_blocks(self, request, auth):
        request.return_value = {"ok": True, "data": {"mac": "aa:bb:cc:00:00:01",
                                                     "number_of_led": 2}}
        with self.assertRaisesRegex(RuntimeError, "LAN authentication failed"):
            live.read_local({"name": "Office Left", "ip": "192.168.1.10", "leds": 2})


if __name__ == "__main__":
    unittest.main()
