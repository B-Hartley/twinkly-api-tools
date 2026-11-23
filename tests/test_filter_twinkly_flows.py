"""Offline host-selection tests. No capture files or credentials required."""
import unittest

from filter_twinkly_flows import relevant


class FlowFilterTests(unittest.TestCase):
    def test_twinkly_cloud_hosts(self):
        self.assertTrue(relevant("api.twinkly.com", set()))
        self.assertTrue(relevant("mqtt.twinkly.com", set()))
        self.assertTrue(relevant("OTHER.TWINKLY.COM.", set()))

    def test_office_lan_only(self):
        ips = {"192.168.21.90", "192.168.21.93"}
        self.assertTrue(relevant("192.168.21.90", ips))
        self.assertTrue(relevant("192.168.21.93", ips))
        self.assertFalse(relevant("192.168.21.89", ips))

    def test_unrelated_traffic_excluded(self):
        self.assertFalse(relevant("apple.com", {"192.168.21.90"}))
        self.assertFalse(relevant("example.com", {"192.168.21.90"}))


if __name__ == "__main__":
    unittest.main()
