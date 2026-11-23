#!/usr/bin/env python3
"""Windows DPAPI-protected Twinkly cloud token cache.

Tokens are encrypted to the current Windows account, never committed to Git,
and only accessed by local scripts. Refresh flow is intentionally not guessed.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import time

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    return blob, buf


def _dpapi(payload, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Persistent cloud tokens require Windows DPAPI; unsupported on this OS")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = ([ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                          ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
                         if decrypt else
                         [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                          ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)])
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, buffer = _blob(payload)
    entropy, entropy_buffer = _blob(b"twinkly-api-tools-cloud-session-v1")
    out = DATA_BLOB()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        ok = function(ctypes.byref(source), None, ctypes.byref(entropy),
                      None, None, flags, ctypes.byref(out))
    else:
        ok = function(ctypes.byref(source), "Twinkly cloud tokens",
                      ctypes.byref(entropy), None, None, flags, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def cache_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TwinklyApiTools" / "cloud-session.dpapi"


def store_session(session, location=None):
    location = Path(location) if location else cache_path()
    access = session.get("access_token")
    refresh = session.get("refresh_token")
    lifetime = session.get("expires_in")
    if not isinstance(access, str) or not access or len(access) > 4096:
        raise ValueError("Missing valid cloud access token")
    if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 4096):
        raise ValueError("Invalid cloud refresh token")
    if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or not 0 < lifetime <= 2592000:
        raise ValueError("Invalid cloud token lifetime")
    private = {"access_token": access, "refresh_token": refresh,
               "expires_at": time.time() + lifetime}
    encrypted = _dpapi(json.dumps(private).encode("utf-8"))
    location.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cloud-", suffix=".tmp", dir=location.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(encrypted)
        os.replace(tmp, location)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_session(location=None):
    location = Path(location) if location else cache_path()
    if not location.is_file():
        return None
    obj = json.loads(_dpapi(location.read_bytes(), decrypt=True))
    if not isinstance(obj, dict) or not isinstance(obj.get("access_token"), str) or not isinstance(obj.get("expires_at"), (int, float)):
        raise ValueError("Invalid cached cloud session")
    return obj


def access_token(location=None):
    session = load_session(location)
    if session and session["expires_at"] > time.time() + 90:
        return session["access_token"]
    return None


def clear_session(location=None):
    location = Path(location) if location else cache_path()
    location.unlink(missing_ok=True)
