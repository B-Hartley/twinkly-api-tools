"""Offline tests for Twinkly's non-invasive MQTT broker probe."""
import argparse
import tempfile
import unittest
from unittest.mock import patch

import probe_mqtt_broker as mqtt
import twinkly_inspector as inspector


class DummySocket:
    def __init__(self, reply=b"\x20\x02\x00\x00"):
        self.reply = reply
        self.sent = []
        self.closed = False

    def settimeout(self, seconds):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, count):
        reply, self.reply = self.reply[:count], self.reply[count:]
        return reply

    def close(self):
        self.closed = True


class MqttProbeTests(unittest.TestCase):
    def test_inspector_uses_authenticated_get(self):
        self.assertEqual(inspector.ENDPOINTS["mqtt_config"], "/xled/v1/mqtt/config")
        self.assertNotIn("mqtt_config", inspector.PUBLIC)

    def test_mqtt_password_redacted_recursively(self):
        raw = {"data": {"broker_host": "mqtt.twinkly.com", "password": "secret-value",
                        "user": "twinkly32", "broker_port": 8883}}
        clean = inspector.scrub(raw)
        self.assertEqual(clean["data"]["password"], "<redacted>")
        self.assertEqual(clean["data"]["user"], "twinkly32")
        self.assertNotIn("secret-value", str(clean))

    def test_connect_without_credentials_never_subscribes_or_publishes(self):
        sock = DummySocket()
        with patch("probe_mqtt_broker.socket.create_connection", return_value=sock):
            output = mqtt.probe(1883, timeout=1)
        self.assertTrue(output["connect_accepted"])
        self.assertFalse(output["subscribe_attempted"])
        self.assertFalse(output["publish_attempted"])
        self.assertEqual(len(sock.sent), 2)
        self.assertEqual(sock.sent[-1], b"\xe0\x00")
        self.assertEqual(sock.sent[0][0], 0x10)
        self.assertNotIn(b"twinkly32", sock.sent[0])
        self.assertTrue(sock.closed)

    def test_username_only_bad_credentials_not_authorised(self):
        sock = DummySocket(reply=b"\x20\x02\x00\x04")
        with patch("probe_mqtt_broker.socket.create_connection", return_value=sock):
            result = mqtt.probe(1883, username="twinkly32", timeout=1)
        self.assertFalse(result["connect_accepted"])
        self.assertEqual(result["connack_code"], 4)
        self.assertIn(b"twinkly32", sock.sent[0])
        self.assertEqual(len(sock.sent), 1)

    def test_custom_ca_is_verified_and_hostname_checked(self):
        sock = DummySocket()
        class Context:
            def wrap_socket(self, connection, server_hostname):
                self_hostname = server_hostname
                if self_hostname != mqtt.HOST:
                    raise AssertionError("Missing TLS hostname verification")
                return connection
        with patch("probe_mqtt_broker.socket.create_connection",
                   return_value=sock), \
             patch("probe_mqtt_broker.ssl.create_default_context",
                   return_value=Context()) as ctx:
            # DummySocket does not implement version(); mimic TLS metadata.
            sock.version = lambda: "TLSv1.3"
            result = mqtt.probe(8883, username="twinkly32", timeout=1,
                                ca_file="reviewed-ca.pem")
        ctx.assert_called_once_with(cafile="reviewed-ca.pem")
        self.assertTrue(result["connect_accepted"])
        self.assertEqual(result["tls_version"], "TLSv1.3")

    def test_refuse_other_ports_and_invalid_username(self):
        for port in (1, 8884, 1884):
            with self.assertRaises(ValueError):
                mqtt.probe(port)
        with self.assertRaises(ValueError):
            mqtt.probe(8883, username="bad username")

    def test_unexpected_response_does_not_claim_auth_success(self):
        sock = DummySocket(reply=b"\x30\x02\x00\x00")
        with patch("probe_mqtt_broker.socket.create_connection", return_value=sock):
            with self.assertRaisesRegex(RuntimeError, "Unexpected MQTT"):
                mqtt.probe(1883)
        self.assertTrue(sock.closed)


if __name__ == "__main__":
    unittest.main()
