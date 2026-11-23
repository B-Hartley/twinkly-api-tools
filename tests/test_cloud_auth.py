"""Offline tests: cloud auth never logs or persists captured credentials."""
import io
import json
import unittest
from unittest.mock import patch

import cloud_auth


class FakeResponse:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, n):
        return json.dumps({"access_token": "fake-access", "refresh_token": "fake-refresh",
                           "expires_in": 3600, "token_type": "Bearer"}).encode()


class FakeOpener:
    def __init__(self):
        self.request = None
    def open(self, request, timeout):
        self.request = request
        return FakeResponse()


class CloudAuthTests(unittest.TestCase):
    @patch("cloud_auth.urllib.request.build_opener")
    def test_login_endpoint_and_token(self, build_opener):
        opener = FakeOpener()
        build_opener.return_value = opener
        self.assertEqual(cloud_auth.authenticate("test@example.invalid", "test-pass"), "fake-access")
        self.assertEqual(opener.request.full_url, "https://api.twinkly.com/v2/auth")
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertEqual(json.loads(opener.request.data), {
            "username": "test@example.invalid", "password": "test-pass"})
        self.assertIsInstance(build_opener.call_args.args[0], cloud_auth.NoRedirect)

    @patch("cloud_auth.urllib.request.build_opener")
    def test_missing_credentials_never_contact_host(self, build_opener):
        with self.assertRaises(ValueError):
            cloud_auth.authenticate("", "password")
        with self.assertRaises(ValueError):
            cloud_auth.authenticate("someone", "")
        build_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
