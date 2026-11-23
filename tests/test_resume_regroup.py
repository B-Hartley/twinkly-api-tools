"""Offline safety tests for partial regroup recovery."""
import json
import unittest
from unittest.mock import patch

import resume_regroup
from tests.test_group_tools import sample_plan


class ResumeTests(unittest.TestCase):
    def test_steps_for_states(self):
        self.assertEqual(resume_regroup.steps_for_state({"group": "none", "layout": "area"}),
                         [resume_regroup.GROUP_API])
        self.assertEqual(resume_regroup.steps_for_state({"group": "none", "layout": "standalone"}),
                         [resume_regroup.LAYOUT_API, resume_regroup.GROUP_API])
        self.assertEqual(resume_regroup.steps_for_state({"group": "expected", "layout": "area"}), [])

    @patch.object(resume_regroup, "request")
    @patch.object(resume_regroup, "authenticate")
    def test_readonly_preview_never_posts(self, authenticate, request):
        plan = sample_plan()
        authenticate.return_value = ("token", {})
        def reply(ip, path, **kwargs):
            member = next(m for m in plan["members"] if m["ip"] == ip)
            if path == resume_regroup.GROUP_API:
                return {"ok": True, "data": {"code": 1000, "group": {"mode": "none"}}}
            if path == resume_regroup.LAYOUT_API:
                layout = member["area_layout"] if member["offset"] == 0 else member["standalone_layout"]
                return {"ok": True, "data": layout}
            raise AssertionError("Unexpected request")
        request.side_effect = reply
        self.assertEqual(resume_regroup.run(
            plan, plan["uid"], 5, False, "does-not-exist.json"), 0)
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET"
                            for call in request.call_args_list))

    @patch.object(resume_regroup, "request")
    @patch.object(resume_regroup, "authenticate")
    def test_rejects_unknown_group(self, authenticate, request):
        plan = sample_plan()
        authenticate.return_value = ("token", {})
        request.return_value = {"ok": True, "data": {"group": {"mode": "slave", "uid": "wrong"}}}
        with self.assertRaisesRegex(RuntimeError, "unexpected group"):
            resume_regroup.read_state(plan["members"][0], plan["uid"], 4, 5)


if __name__ == "__main__":
    unittest.main()
