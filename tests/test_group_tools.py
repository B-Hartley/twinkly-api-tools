"""Offline regression tests for read-only probe and guarded group operations."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import group_test
import build_group_plan
import probe_api


def sample_plan():
    def layout(source, start):
        return {"source": source, "synthesized": False,
                "coordinates": [{"x": float(start + i), "y": 0.0, "z": 0.0}
                                for i in range(2)]}
    return {"schema": 1, "group_name": "Disposable", "uid": "12345678-1234-1234-1234-123456789abc",
            "size": 4, "preverified_backup": True,
            "members": [
                {"name": "A", "ip": "192.168.1.11", "leds": 2, "offset": 0,
                 "area_layout": layout("2d", 0), "standalone_layout": layout("3d", 0)},
                {"name": "B", "ip": "192.168.1.12", "leds": 2, "offset": 2,
                 "area_layout": layout("2d", 2), "standalone_layout": layout("3d", 2)}
            ]}


class ProbeTests(unittest.TestCase):
    def test_excludes_wifi_scan(self):
        self.assertIn("network_scan", probe_api.EXCLUDED)
        self.assertTrue(all(path.startswith("/xled/v1/")
                            for path in probe_api.ENDPOINTS.values()))

    def test_schema_types_and_firmware(self):
        self.assertEqual(probe_api.schema({"leds": [{"x": 1}, {"x": 2}]}),
                         {"leds": {"type": "list", "count": 2, "sample": {"x": "int"}}})
        result = probe_api.firmware_info({"data": {"product_code": "TWW", "fw_family": "G"}},
                                         {"data": {"version": "2.11.0"}})
        self.assertEqual(result["fw_version"]["version"], "2.11.0")


class BuilderTests(unittest.TestCase):
    def test_case_insensitive_names_and_spaces(self):
        controllers = {
            "192.168.1.11": {"ip": "192.168.1.11", "name": "Office Left"},
            "192.168.1.12": {"ip": "192.168.1.12", "name": "Office Right"},
        }
        selected = build_group_plan.select_members(
            controllers, ["Office left", "  OFFICE   right  "])
        self.assertEqual([d["ip"] for d in selected],
                         ["192.168.1.11", "192.168.1.12"])

    def test_missing_name_shows_available_controllers(self):
        controllers = {"192.168.1.11": {"ip": "192.168.1.11", "name": "Office Left"}}
        with self.assertRaisesRegex(ValueError, "Office Left"):
            build_group_plan.select_members(controllers, ["Unknown"])


class GroupTests(unittest.TestCase):
    def test_correct_sequences(self):
        plan = sample_plan()
        actions = list(group_test.payloads(plan, "regroup", plan["uid"]))
        self.assertEqual([p for p, _ in actions[0][1]],
                         [group_test.LAYOUT_API, group_test.GROUP_API])
        self.assertEqual(actions[1][1][1][1]["group"]["offset"], 2)
        ungroup = list(group_test.payloads(plan, "ungroup", plan["uid"]))
        self.assertEqual(ungroup[0][1][0][1]["group"]["mode"], "none")

    def test_plan_rejects_invalid_offsets(self):
        plan = sample_plan()
        plan["members"][1]["offset"] = 5
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(ValueError):
                group_test.load_plan(path)

    def test_plan_rejects_unverified_backup(self):
        plan = sample_plan()
        plan["preverified_backup"] = False
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaises(ValueError):
                group_test.load_plan(path)

    @patch.object(group_test, "request")
    def test_dry_run_does_not_contact_devices(self, mock_request):
        plan = sample_plan()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plan.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            with patch("sys.argv", ["group_test.py", "ungroup", "--plan", str(path)]):
                self.assertEqual(group_test.main(), 0)
        mock_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
