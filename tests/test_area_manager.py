"""Offline safety/dispatch tests for independent Area actions."""
import unittest
from unittest.mock import patch

import area_manager


class AreaManagerTests(unittest.TestCase):
    @patch("area_manager.cycle.run", return_value=0)
    def test_ungroup_never_auto_regroups(self, handler):
        rc = area_manager.main(["ungroup", "--group", "Office Group",
                                "--execute", "--acknowledge-risk",
                                "--confirm-group", "Office Group"])
        self.assertEqual(rc, 0)
        args = handler.call_args.args[0]
        self.assertTrue(args.stop_after_ungroup)
        self.assertFalse(args.stop_after_group)
        self.assertFalse(args.start_ungrouped)
        self.assertTrue(args.execute)

    @patch("area_manager.reverse.run_reverse", return_value=0)
    def test_group_never_auto_ungroups(self, handler):
        rc = area_manager.main(["group", "--group", "Office Group",
                                "--execute", "--acknowledge-risk",
                                "--confirm-group", "Office Group"])
        self.assertEqual(rc, 0)
        args = handler.call_args.args[0]
        self.assertTrue(args.stop_after_group)
        self.assertFalse(args.stop_after_ungroup)
        self.assertTrue(args.start_ungrouped)

    @patch("area_manager.cycle.run", return_value=0)
    def test_ungroup_defaults_readonly(self, handler):
        self.assertEqual(area_manager.main(["ungroup", "--group", "Office Group"]), 0)
        self.assertFalse(handler.call_args.args[0].execute)

    @patch("area_manager.maps.from_devices", return_value=(2, 0))
    def test_capture_original_standalone_3d(self, capture):
        rc = area_manager.main(["capture-3d", "--devices", "devices.json"])
        self.assertEqual(rc, 0)
        self.assertEqual(capture.call_args.args[0], "devices.json")

    @patch("area_manager.maps.from_plan", return_value=2)
    def test_capture_saved_plan_3d_requires_opt_in(self, capture):
        rc = area_manager.main(["capture-3d", "--plan", "private-area-plan.json",
                                "--allow-saved-plan"])
        self.assertEqual(rc, 0)
        self.assertTrue(capture.call_args.kwargs["allow_saved_plan"])

    def test_timeout_is_bounded(self):
        with self.assertRaises(SystemExit):
            area_manager.main(["verify", "--timeout", "90"])


if __name__ == "__main__":
    unittest.main()
