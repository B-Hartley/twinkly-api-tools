"""Offline tests for explicit CLI Twinkly controller selection."""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from twinkly_inspector import get_devices


class SelectionTests(unittest.TestCase):
    def test_ips_override_implicit_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "devices.json").write_text(json.dumps({
                "devices": [{"name": "Duplicated Office Left", "ip": "192.168.21.90"},
                            {"name": "Another", "ip": "192.168.21.55"}]}))
            with patch("os.getcwd", return_value=temp), patch("pathlib.Path.cwd", return_value=root):
                # The default configuration must not even be opened.
                with patch("pathlib.Path.read_text", side_effect=AssertionError("config read")):
                    result = get_devices(argparse.Namespace(
                        ips=["192.168.21.90", "192.168.21.93"], config=None))
            self.assertEqual([d["ip"] for d in result],
                             ["192.168.21.90", "192.168.21.93"])
            self.assertEqual([d["name"] for d in result],
                             ["192.168.21.90", "192.168.21.93"])

    def test_explicit_config_ignored_when_ips_present(self):
        result = get_devices(argparse.Namespace(
            ips=["192.168.21.90"], config="nonexistent-config.json"))
        self.assertEqual(len(result), 1)

    def test_duplicate_cli_ips_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate controller IP"):
            get_devices(argparse.Namespace(
                ips=["192.168.21.90", "192.168.21.90"], config=None))

    def test_config_mode_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "devices.json"
            target.write_text(json.dumps({"devices": [
                {"name": "Office Left", "ip": "192.168.21.90"}]}))
            result = get_devices(argparse.Namespace(ips=None, config=str(target)))
            self.assertEqual(result, [{"ip": "192.168.21.90", "name": "Office Left"}])


if __name__ == "__main__":
    unittest.main()
