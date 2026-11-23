#!/usr/bin/env python3
"""Extract only Twinkly hosts and specified LAN controllers from mitmproxy flows.

Keeps complete matching HTTP flows (including sensitive tokens and payloads).
This is filtering, NOT redaction. The unfiltered source is never deleted.
"""
import argparse
from collections import Counter
from pathlib import Path
import sys


DEFAULT_IPS = ("192.168.21.90", "192.168.21.93")


def relevant(host, ips):
    host = host.casefold().rstrip(".")
    return "twinkly" in host or host in ips


def filter_flows(source, destination, ips):
    from mitmproxy import http, io

    count = 0
    kept = 0
    counts = Counter()
    with source.open("rb") as src, destination.open("xb") as out:
        reader = io.FlowReader(src)
        writer = io.FlowWriter(out)
        for flow in reader.stream():
            count += 1
            if not isinstance(flow, http.HTTPFlow) or flow.request is None:
                continue
            host = (flow.request.pretty_host or flow.request.host or "").casefold()
            if not relevant(host, ips):
                continue
            writer.add(flow)
            kept += 1
            counts[host] += 1
    return count, kept, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--ips", nargs="+", default=list(DEFAULT_IPS))
    args = parser.parse_args()

    if not args.source.is_file():
        parser.error("Source capture file does not exist")
    if args.source.resolve() == args.destination.resolve():
        parser.error("Refusing to overwrite the original capture")
    if args.destination.exists():
        parser.error("Destination already exists; choose a different name")
    if not args.destination.parent.is_dir():
        parser.error("Destination directory does not exist")
    try:
        total, kept, hosts = filter_flows(args.source, args.destination, set(args.ips))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Flows read: {total}; Twinkly/LAN flows retained: {kept}")
    for host, number in sorted(hosts.items()):
        print(f"  {host}: {number}")
    print(f"Filtered capture: {args.destination}")
    print("WARNING: matching flows may contain login/access tokens or device credentials.")
    print("This tool FILTERS flows; it does NOT redact them. Keep raw and filtered files private.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
