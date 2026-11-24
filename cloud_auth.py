#!/usr/bin/env python3
"""Interactive Twinkly cloud password login (unofficial, experimental).

POSTs only to the fixed HTTPS login and refresh endpoints.
Never logs credentials or follows redirects. The caller may explicitly
store access and refresh tokens in the Windows DPAPI-protected token cache.
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
    # Keep the refresh token securely for subsequent renewal.
    if return_session:
        lifetime = data.get('expires_in')
        if not isinstance(lifetime, (int, float)) or isinstance(lifetime, bool) or not 0 < lifetime <= 2592000:
            lifetime = 0
        refresh = data.get('refresh_token')
        if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 4096):
            raise RuntimeError('Invalid cloud refresh token')
        return {'access_token': token, 'refresh_token': refresh, 'expires_in': lifetime}
    return token


def interactive_login(username=None, timeout=10, return_session=False):
    username = username or input("Twinkly account email: ").strip()
    password = getpass.getpass("Twinkly password (not displayed): ")
    try:
        return authenticate(username, password, timeout, return_session=return_session)
    finally:
        # Python cannot guarantee erasure of immutable strings from memory.
        del password


def refresh_session(refresh_token, timeout=10):
    """Confirmed Twinkly token-only JSON refresh; never retry a rotating token."""
    if not isinstance(refresh_token,str) or not refresh_token or len(refresh_token)>4096 or any(c.isspace() for c in refresh_token):
        raise ValueError("Missing valid refresh token; use --login")
    if not 0<timeout<=30:
        raise ValueError("Timeout out of range")
    req=urllib.request.Request(BASE+"/v2/auth/refresh",
        data=json.dumps({"refresh_token":refresh_token}).encode("utf-8"),
        headers={"Content-Type":"application/json","Accept":"application/json",
                 "User-Agent":"twinkly-api-tools-cloud-auth/0.2"},method="POST")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=timeout) as response:
            raw=response.read(MAX_RESPONSE+1)
            if response.status!=200 or len(raw)>MAX_RESPONSE:
                raise RuntimeError("Unexpected cloud refresh response")
        data=json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Cloud refresh HTTP {exc.code}; use --login if renewal is rejected") from None
    except (ValueError,OSError) as exc:
        raise RuntimeError("Cloud refresh response unavailable or invalid; do not blindly retry a rotating token") from None
    if not isinstance(data,dict) or str(data.get("token_type","")).casefold()!="bearer":
        raise RuntimeError("Unexpected cloud refresh reply")
    access=data.get("access_token")
    replacement=data.get("refresh_token")
    lifetime=data.get("expires_in")
    for token in (access,replacement):
        if not isinstance(token,str) or not token or len(token)>4096 or any(c.isspace() for c in token):
            raise RuntimeError("Invalid renewed cloud token")
    if isinstance(lifetime,bool) or not isinstance(lifetime,(int,float)) or not 0<lifetime<=2592000:
        raise RuntimeError("Invalid renewed token lifetime")
    return {"access_token":access,"refresh_token":replacement,"expires_in":lifetime}
