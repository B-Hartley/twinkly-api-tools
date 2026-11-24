#!/usr/bin/env python3
"""One-shot, read-only MQTT connection/authentication diagnostic for Twinkly.

Only connects to mqtt.twinkly.com:1883 or :8883. Never subscribes,
publishes, sends Twinkly commands, or uses a controller's MQTT client ID.
No passwords supported or persisted. TLS certificate verification is mandatory.
"""
import argparse
import json
import secrets
import socket
import ssl
import sys

HOST = "mqtt.twinkly.com"
PORTS = (1883, 8883)
REASONS = {
    0: "Connection accepted (does NOT establish subscription permissions)",
    1: "Unacceptable MQTT protocol version",
    2: "Client identifier rejected",
    3: "Server unavailable",
    4: "Bad username or password",
    5: "Not authorised",
}


def utf8_field(text):
    blob = text.encode("utf-8")
    if len(blob) > 65535:
        raise ValueError("MQTT text field too long")
    return len(blob).to_bytes(2, "big") + blob


def remaining_length(value):
    if not 0 <= value < 268435456:
        raise ValueError("MQTT packet too large")
    result = bytearray()
    while True:
        value, digit = divmod(value, 128)
        result.append(digit | (128 if value else 0))
        if not value:
            return bytes(result)


def connect_packet(client_id, username=None):
    # MQTT v3.1.1, clean session, keepalive 15 sec. Deliberately no password.
    flags = 0x02 | (0x80 if username else 0)
    variable = utf8_field("MQTT") + bytes([4, flags, 0, 15])
    payload = utf8_field(client_id)
    if username:
        payload += utf8_field(username)
    body = variable + payload
    return bytes([0x10]) + remaining_length(len(body)) + body


def probe(port=8883, username=None, timeout=5, ca_file=None):
    if port not in PORTS:
        raise ValueError("Only Twinkly's documented ports 1883/8883 supported")
    if username is not None and (not username or len(username) > 64 or
                                 any(not c.isalnum() and c not in "_-" for c in username)):
        raise ValueError("Invalid MQTT username")
    result = {
        "broker": HOST,
        "port": port,
        "transport": "TLS, verified" if port == 8883 else "plaintext TCP",
        "username": username if username else None,
        "password_sent": False,
        "subscribe_attempted": False,
        "publish_attempted": False,
        "connect_accepted": False,
    }
    client_id = "inspect-" + secrets.token_hex(8)
    sock = socket.create_connection((HOST, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        if port == 8883:
            # A user-reviewed CA bundle extends trust without disabling hostname
            # or certificate-chain verification.
            context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=HOST)
            result["tls_version"] = sock.version()
        sock.sendall(connect_packet(client_id, username=username))
        # MQTT 3.1.1 CONNACK must be fixed 4-byte packet.
        reply = b""
        while len(reply) < 4:
            part = sock.recv(4 - len(reply))
            if not part:
                raise RuntimeError("Broker closed connection without MQTT CONNACK")
            reply += part
        if len(reply) != 4 or reply[0] != 0x20 or reply[1] != 0x02 or reply[2] & ~0x01:
            raise RuntimeError("Unexpected MQTT response; cannot establish auth")
        code = reply[3]
        result["connack_code"] = code
        result["connack_reason"] = REASONS.get(code, "Unknown MQTT return code")
        result["connect_accepted"] = code == 0
        if code == 0:
            # Normal client DISCONNECT, no retained subscription or publish.
            sock.sendall(b"\xe0\x00")
        return result
    finally:
        sock.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, choices=PORTS, default=8883)
    p.add_argument("--username", help="Optional documented device MQTT username, without password")
    p.add_argument("--ca-file", help="Optional independently verified CA PEM bundle for TLS trust")
    p.add_argument("--timeout", type=float, default=5)
    args = p.parse_args(argv)
    if not 0.5 <= args.timeout <= 15:
        p.error("--timeout must be between 0.5 and 15 seconds")
    try:
        result = probe(args.port, args.username, args.timeout, ca_file=args.ca_file)
        print(json.dumps(result, indent=2, sort_keys=True))
        if result["connect_accepted"]:
            print("Connection accepted; subscription to any specific device is NOT tested.")
        return 0
    except (OSError, ValueError, RuntimeError, ssl.SSLError) as exc:
        print("BROKER CONNECTION NOT ESTABLISHED:", type(exc).__name__, str(exc),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
