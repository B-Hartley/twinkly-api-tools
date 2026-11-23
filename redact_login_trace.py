#!/usr/bin/env python3
"""Create a credential-free Twinkly cloud authentication trace summary.

Run locally against a mitmproxy .flows recording. Reports method, host, URL
path (no query values), HTTP status, content type, and JSON key names ONLY.
Never outputs request/response values, headers, cookies, tokens or credentials.
Requires the locally installed mitmproxy package.
"""
import argparse
import json
from pathlib import Path
import sys

from mitmproxy import http, io


def key_shape(raw, content_type):
    if "json" not in (content_type or "").lower():
        return "<non-JSON or unavailable>"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return "<JSON parse failed>"
    if isinstance(parsed, dict):
        keys = sorted(str(k) for k in parsed)
        return ", ".join(keys)[:500]
    if isinstance(parsed, list):
        return f"<JSON array with {len(parsed)} entries>"
    return "<JSON scalar>"


def scan(path):
    entries = []
    with path.open("rb") as stream:
        for flow in io.FlowReader(stream).stream():
            if not isinstance(flow, http.HTTPFlow) or not flow.request:
                continue
            request = flow.request
            host = (request.pretty_host or "").lower()
            if host != "api.twinkly.com":
                continue
            response = flow.response
            entries.append({
                "method": request.method,
                "path_no_query": request.path.split("?", 1)[0],
                "status": response.status_code if response else None,
                "request_json_keys": key_shape(request.raw_content, request.headers.get("content-type", "")),
                "response_json_keys": key_shape(response.raw_content, response.headers.get("content-type", ""))
                    if response else "<no response>",
                "request_authorization_header_present": "authorization" in request.headers,
            })
    return entries


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("capture", type=Path)
    p.add_argument("--output", type=Path, default=Path("twinkly-login-redacted.json"))
    args = p.parse_args()
    if not args.capture.is_file():
        p.error("Capture does not exist")
    if args.capture.resolve() == args.output.resolve():
        p.error("Output must not overwrite the capture")
    data = scan(args.capture)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(data)} api.twinkly.com exchanges, values omitted.")
    print(f"Safe summary: {args.output}")
    print("Review manually before sharing: endpoints/JSON key names might still reveal sensitive metadata.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
