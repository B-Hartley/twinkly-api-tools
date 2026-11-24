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

    def test_legacy_ca_requires_explicit_tls_and_ca_file(self):
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            mqtt.probe(8883, legacy_ca=True)
        with self.assertRaisesRegex(ValueError, "requires TLS"):
            mqtt.probe(1883, ca_file="ca.pem", legacy_ca=True)

    def test_legacy_ca_only_removes_strict_flag(self):
        import ssl
        if not hasattr(ssl, "VERIFY_X509_STRICT"):
            self.skipTest("OpenSSL has no strict flag")
        sock = DummySocket()
        sock.version = lambda: "TLSv1.3"
        class Context:
            verify_flags = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_TRUSTED_FIRST
            verify_mode = ssl.CERT_REQUIRED
            check_hostname = True
            def wrap_socket(self, connection, server_hostname):
                self.assert_hostname = server_hostname
                return connection
        ctx = Context()
        with patch("probe_mqtt_broker.socket.create_connection", return_value=sock), \
             patch("probe_mqtt_broker.ssl.create_default_context", return_value=ctx) as create:
            result = mqtt.probe(8883, username="twinkly32", timeout=1,
                                ca_file="reviewed-ca.pem", legacy_ca=True)
        create.assert_called_once_with(cafile="reviewed-ca.pem")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.assert_hostname, mqtt.HOST)
        self.assertFalse(ctx.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertTrue(ctx.verify_flags & ssl.VERIFY_X509_TRUSTED_FIRST)
        self.assertTrue(result["legacy_ca_compatibility"])

    def test_reset_reports_precise_mqtt_stage(self):
        class ResetSocket(DummySocket):
            def recv(self, count):
                raise ConnectionResetError(10054, "Remote host reset")
        sock = ResetSocket()
        with patch("probe_mqtt_broker.socket.create_connection", return_value=sock):
            with self.assertRaisesRegex(RuntimeError, "Failed at mqtt-connack-read"):
                mqtt.probe(1883, timeout=1)
        self.assertTrue(sock.closed)

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
