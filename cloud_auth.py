#!/usr/bin/env python3
"""Interactive Twinkly cloud password login (unofficial, experimental).

POSTs only to the fixed HTTPS endpoint https://api.twinkly.com/v2/auth.
Returns the short-lived access token in memory only; never logs credentials,
returns the refresh token, follows redirects or saves tokens to disk.
"""
import getpass
import json
import urllib.error
import urllib.request

BASE = "https://api.twinkly.com"
MAX_RESPONSE = 128 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Cloud redirect refused to protect credentials")


def authenticate(username, password, timeout=10, return_session=False):
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        raise ValueError("Username and password are required")
    if not 0 < timeout <= 30:
        raise ValueError("Timeout out of range")
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/v2/auth", data=payload, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "twinkly-api-tools-cloud-auth/0.1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"Cloud login HTTP {response.status}")
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise RuntimeError("Authentication response exceeds safety limit")
            data = json.loads(body)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Cloud login HTTP {error.code}") from None
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected cloud login reply")
    if data.get("token_type", "").casefold() != "bearer":
        raise RuntimeError("Unexpected cloud token type")
    token = data.get("access_token")
    if not isinstance(token, str) or not token or len(token) > 4096 or any(c.isspace() for c in token):
        raise RuntimeError("Cloud login did not return valid access token")
    # Deliberately discard refresh_token: endpoint for refresh is unverified.
    if return_session:
        lifetime = data.get('expires_in')
        if not isinstance(lifetime, (int, float)) or isinstance(lifetime, bool) or not 0 < lifetime <= 2592000:
            lifetime = 0
        return {'access_token': token, 'expires_in': lifetime}
    return token


def interactive_login(username=None, timeout=10):
    username = username or input("Twinkly account email: ").strip()
    password = getpass.getpass("Twinkly password (not displayed): ")
    try:
        return authenticate(username, password, timeout)
    finally:
        # Python cannot guarantee erasure of immutable strings from memory.
        del password
