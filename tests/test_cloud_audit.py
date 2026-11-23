"""Offline tests for Twinkly cloud audit parsing and strict request controls."""
import base64
import gzip
import json
import unittest
from unittest.mock import patch

import cloud_audit


class CloudAuditTests(unittest.TestCase):
    def test_decode_layout(self):
        coords = [{"x": 0.1, "y": 0.2, "z": 0.3}]
        encoded = base64.b64encode(gzip.compress(json.dumps(coords).encode())).decode()
        self.assertEqual(cloud_audit.decode_coords(encoded), coords)

    def test_objects_unwrap(self):
        sample = [{"id": 1}]
        self.assertEqual(cloud_audit.objects_from({"data": {"objects": sample}}), sample)
        with self.assertRaises(ValueError):
            cloud_audit.objects_from({"unknown": sample})

    @patch("cloud_audit.urllib.request.build_opener")
    def test_unrecognised_path_cannot_contact_server(self, opener):
        with self.assertRaises(ValueError):
            cloud_audit.cloud_get("https://evil.example/token", "secret")
        opener.assert_not_called()

    def test_summary_does_not_expose_sensitive_device_fields(self):
        member = {"name": "Controller", "standalone_layout": {
            "source": "3d", "synthesized": False,
            "coordinates": [{"x": 1, "y": 2, "z": 3}]},
            "area_layout": {"source": "2d", "synthesized": False,
                            "coordinates": [{"x": 0, "y": 0, "z": 0}]}}
        data = {"name": "Safe", "id": 123, "ledCount": 1,
                "devices": [{"device": {"name": "Controller",
                                        "password": "DO-NOT-LEAK",
                                        "mac": "AA:BB:CC:DD:EE:FF"}}]}
        summary = cloud_audit.summarise(data, {"members": [member]})
        self.assertNotIn("DO-NOT-LEAK", json.dumps(summary))
        self.assertNotIn("AA:BB:CC:DD:EE:FF", json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
