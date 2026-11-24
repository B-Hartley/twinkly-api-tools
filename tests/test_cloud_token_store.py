"""Offline DPAPI cache contract tests; Windows encryption is mocked."""
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_token_store


class CloudTokenStoreTests(unittest.TestCase):
    def test_round_trip_encrypted_cache_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.dpapi"
            def encrypt(payload, decrypt=False):
                return (base64.b64decode(payload[4:]) if decrypt else
                        b"ENC:" + base64.b64encode(payload))
            with patch.object(cloud_token_store, "_dpapi", side_effect=encrypt):
                cloud_token_store.store_session({
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600}, path)
                self.assertNotIn(b"access-token", path.read_bytes())
                self.assertNotIn(b"refresh-token", path.read_bytes())
                self.assertEqual(cloud_token_store.access_token(path), "access-token")
                self.assertEqual(cloud_token_store.load_session(path)["refresh_token"], "refresh-token")
                cloud_token_store.clear_session(path)
                self.assertFalse(path.exists())

    def test_expired_token_is_not_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.dpapi"
            expired = json.dumps({"access_token": "expired",
                "refresh_token": "refresh", "expires_at": time.time() - 5}).encode()
            path.write_bytes(b"ENC:" + expired)
            with patch.object(cloud_token_store, "_dpapi",
                              side_effect=lambda payload, decrypt=False: payload[4:]):
                self.assertIsNone(cloud_token_store.access_token(path))

    def test_rejects_missing_or_invalid_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                cloud_token_store.store_session({
                    "access_token": "", "expires_in": 3600}, Path(temp) / "x")
            with self.assertRaises(ValueError):
                cloud_token_store.store_session({
                    "access_token": "a", "expires_in": -5}, Path(temp) / "x")


if __name__ == "__main__":
    unittest.main()
