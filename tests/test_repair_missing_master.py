"""Offline safety tests for repairing only a missing master role."""
import copy
import unittest
from unittest.mock import patch

import cloud_group_preflight as preflight
import repair_missing_master as repair
from test_cloud_group_preflight import stage


UID = "12345678-1234-1234-1234-123456789abc"


def candidate():
    snap = stage(grouped=True)
    left = snap[preflight.FILES[1]]
    right = snap[preflight.FILES[2]]
    left["group"]["group"] = {"mode": "none"}
    right["group"]["group"]["uid"] = UID
    plan = {
        "group_name": "Office Windows", "size": 4,
        "members": [
            {"name": "Office Left", "ip": "192.168.1.10", "leds": 2, "offset": 0},
            {"name": "Office Right", "ip": "192.168.1.11", "leds": 2, "offset": 2},
        ]
    }
    report = preflight.evaluate(snap)
    return report, plan, snap


class MasterRepairTests(unittest.TestCase):
    def test_exact_partial_state_only(self):
        report, plan, snap = candidate()
        self.assertEqual(set(report["issues"]), repair.EXPECTED_ISSUES)
        payload, fingerprint = repair.repair_candidate(report, plan, snap)
        self.assertEqual(payload, {"group": {"mode": "master", "size": 4,
                                              "offset": 0, "uid": UID, "compat_mode": 0}})
        self.assertTrue(fingerprint)

    def test_group_label_is_not_identity(self):
        report, plan, snap = candidate()
        repair.validate_group_label("Office Windows", report, plan)
        repair.validate_group_label("Area", report, plan)
        with self.assertRaises(ValueError):
            repair.validate_group_label("Unrelated area", report, plan)

    def test_master_already_joined_must_not_repair(self):
        report, plan, snap = candidate()
        snap[preflight.FILES[1]]["group"]["group"].update(
            {"mode": "master", "uid": UID, "size": 4, "offset": 0})
        result = preflight.evaluate(snap)
        with self.assertRaises(ValueError):
            repair.repair_candidate(result, plan, snap)

    def test_wrong_layout_blocks(self):
        report, plan, snap = candidate()
        snap[preflight.FILES[1]]["layout"]["coordinates"][0]["x"] += 1
        report = preflight.evaluate(snap)
        with self.assertRaises(ValueError):
            repair.repair_candidate(report, plan, snap)

    def test_wrong_slave_uuid_blocks(self):
        report, plan, snap = candidate()
        snap[preflight.FILES[2]]["group"]["group"]["uid"] = "not-a-uuid"
        with self.assertRaises(ValueError):
            repair.repair_candidate(report, plan, snap)

    @patch("repair_missing_master.request")
    @patch("repair_missing_master.authenticate")
    @patch("repair_missing_master.collect")
    def test_dry_run_never_writes(self, collect, auth, request):
        report, plan, snap = candidate()
        collect.return_value = (report, plan, snap)
        self.assertEqual(repair.run("Office Windows", "private-plan.json"), 0)
        self.assertEqual(collect.call_count, 1)
        request.assert_not_called()
        auth.assert_not_called()

    @patch("repair_missing_master.request")
    @patch("repair_missing_master.authenticate", return_value=("fake-token", {}))
    @patch("repair_missing_master.collect")
    def test_execute_only_posts_to_master_once(self, collect, auth, request):
        report, plan, snap = candidate()
        complete = copy.deepcopy(snap)
        complete[preflight.FILES[1]]["group"]["group"] = {
            "mode": "master", "size": 4, "offset": 0, "uid": UID}
        post_report = preflight.evaluate(complete)
        self.assertEqual(post_report["result"], "PASS_READ_ONLY")
        collect.side_effect = [
            (report, plan, snap), (report, plan, snap),
            (post_report, plan, complete)]
        request.side_effect = [
            {"ok": True, "data": {"code": 1000, "group": {"mode": "none"}}},
            {"ok": True, "data": {"code": 1000}},
        ]
        result = repair.run(
            "Office Windows", "private-plan.json", execute=True,
            acknowledged=True, group_confirm="Office Windows",
            master_confirm="Office Left")
        self.assertEqual(result, 0)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "192.168.1.10")
        self.assertEqual(request.call_args_list[0].kwargs.get("method", "GET"), "GET")
        call = request.call_args_list[1]
        self.assertEqual(call.args, ("192.168.1.10", repair.GROUP_ENDPOINT))
        self.assertEqual(call.kwargs["method"], "POST")
        self.assertEqual(call.kwargs["payload"]["group"]["mode"], "master")

    @patch("repair_missing_master.request")
    @patch("repair_missing_master.authenticate")
    @patch("repair_missing_master.collect")
    def test_state_change_between_collects_blocks_post(self, collect, auth, request):
        report, plan, snap = candidate()
        changed = copy.deepcopy(snap)
        changed[preflight.FILES[2]]["layout"]["coordinates"][0]["x"] += 0.5
        new_report = preflight.evaluate(changed)
        collect.side_effect = [
            (report, plan, snap), (new_report, plan, changed)]
        with self.assertRaises(ValueError):
            repair.run("Office Windows", "p.json", execute=True,
                       acknowledged=True, group_confirm="Office Windows",
                       master_confirm="Office Left")
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
