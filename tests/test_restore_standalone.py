"""Offline checks for safe forced restore of known standalone LED mappings."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import restore_standalone as restore


def plan():
    def layout(source):
        return {"source": source, "synthesized": False,
                "coordinates": [{"x": 0.2, "y": 0.4, "z": 0.3}]}
    return {"group_name": "Office Windows", "members": [
        {"ip": "192.168.1.11", "name": "Office Left", "leds": 1,
         "area_layout": layout("2d"), "standalone_layout": layout("3d")},
        {"ip": "192.168.1.12", "name": "Office Right", "leds": 1,
         "area_layout": layout("2d"), "standalone_layout": layout("3d")}]}


class RestoreTests(unittest.TestCase):
    @patch.object(restore, "authenticate", return_value=("token", {}))
    @patch.object(restore, "request")
    def test_preview_is_readonly(self, req, _auth):
        p = plan()
        def response(ip, path, **kw):
            if path == restore.GESTALT:
                return {"ok": True, "data": {"number_of_led": 1}}
            if path == restore.GROUP_API:
                return {"ok": True, "data": {"group": {"mode": "none"}}}
            return {"ok": True, "data": p["members"][0]["area_layout"]}
        req.side_effect = response
        with tempfile.TemporaryDirectory() as d:
            code = restore.run(p, 5, False, Path(d) / "backups", Path(d) / "log.json")
            self.assertEqual(code, 0)
            self.assertFalse((Path(d) / "backups").exists())
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET" for call in req.call_args_list))

    @patch.object(restore, "authenticate", return_value=("token", {}))
    @patch.object(restore, "request")
    def test_grouped_controller_refuses_any_writes(self, req, _auth):
        def response(ip, path, **kw):
            if path == restore.GESTALT:
                return {"ok": True, "data": {"number_of_led": 1}}
            if path == restore.GROUP_API:
                return {"ok": True, "data": {"group": {"mode": "slave"}}}
            return {"ok": True, "data": plan()["members"][0]["area_layout"]}
        req.side_effect = response
        with tempfile.TemporaryDirectory() as d:
            code = restore.run(plan(), 5, True, Path(d) / "backups", Path(d) / "log.json")
            self.assertEqual(code, 2)
            self.assertFalse((Path(d) / "backups").exists())
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET" for call in req.call_args_list))


if __name__ == "__main__":
    unittest.main()
