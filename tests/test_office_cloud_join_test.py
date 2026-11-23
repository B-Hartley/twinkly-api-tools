"""Offline regression for partial Office join observation and resume."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import office_cloud_join_test as mod


class OfficeJoinCycleTests(unittest.TestCase):
    def test_import_and_error_reporting(self):
        self.assertTrue(hasattr(mod, "urllib"))
        self.assertTrue(hasattr(mod.urllib, "error"))

    def test_reject_partial_join(self):
        partial = {"local": [{"name": "Left", "state": "joined", "group_uid": "a"},
                             {"name": "Right", "state": "unjoined", "group_uid": None}]}
        with self.assertRaisesRegex(RuntimeError, "Left=joined, Right=unjoined"):
            mod.validate_phase(partial, "joined")

    def test_reject_different_group_ids(self):
        partial = {"local": [{"name": "Left", "state": "joined", "group_uid": "a"},
                             {"name": "Right", "state": "joined", "group_uid": "b"}]}
        with self.assertRaisesRegex(RuntimeError, "same group UID"):
            mod.validate_phase(partial, "joined")

    def test_retry_preserves_partial_snapshot(self):
        done = {"local": [{"name": "Left", "state": "joined", "group_uid": "u"},
                          {"name": "Right", "state": "joined", "group_uid": "u"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "02-joined-attempt-01"
            old.mkdir()
            (old / "summary-PRIVATE.json").write_text(json.dumps({
                "local": [{"name": "Left", "state": "joined", "group_uid": "u"},
                          {"name": "Right", "state": "unjoined", "group_uid": None}]}))
            with patch.object(mod, "capture", return_value=done) as cap:
                actual = mod.capture_until_valid({}, "token", 5, root, "02-joined", "joined")
            self.assertEqual(actual, done)
            self.assertEqual(cap.call_count, 1)
            self.assertTrue((old / "summary-PRIVATE.json").is_file())


if __name__ == "__main__":
    unittest.main()
