"""Offline smoke tests; no Twinkly hardware needed."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import twinkly_inspector as inspector


class InspectorTests(unittest.TestCase):
    def test_rejects_public_ip(self):
        with self.assertRaises(ValueError):
            inspector.validate_ip("8.8.8.8")

    def test_rejects_duplicate_device(self):
        with self.assertRaises(ValueError):
            inspector.get_devices(SimpleNamespace(ips=["192.168.1.2", "192.168.1.2"], config=None))

    def test_scrubs_auth_credentials(self):
        obj = {"authentication_token": "secret", "nested": [{"challenge-response": "value", "uuid": "ok"}]}
        result = inspector.scrub(obj)
        self.assertEqual(result["authentication_token"], "<redacted>")
        self.assertEqual(result["nested"][0]["challenge-response"], "<redacted>")
        self.assertEqual(result["nested"][0]["uuid"], "ok")

    def test_digest_independent_of_key_order(self):
        self.assertEqual(inspector.digest({"a": 1, "b": 2}), inspector.digest({"b": 2, "a": 1}))

    @patch.object(inspector, "request")
    def test_auth_token_never_written(self, mock_request):
        mock_request.side_effect = [
            {"ok": True, "data": {"authentication_token": "private", "challenge-response": "proof"}},
            {"ok": True, "data": {"code": 1000}}
        ]
        token, err = inspector.authenticate("192.168.1.5", 5)
        self.assertEqual(token, "private")
        self.assertEqual(err, {})
        self.assertEqual(mock_request.call_args.kwargs["payload"], {"challenge-response": "proof"})

    def test_compare_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / "before", Path(tmp) / "after"]
            for root in roots:
                data = {"controllers": [{"ip": "192.168.1.5", "endpoints": {
                    name: {"ok": True, "sha256": "constant"} for name in inspector.ENDPOINTS}}]}
                inspector.save_json(root / "manifest.json", data)
            self.assertEqual(inspector.compare(SimpleNamespace(before=str(roots[0]), after=str(roots[1]))), 0)


if __name__ == "__main__":
    unittest.main()
