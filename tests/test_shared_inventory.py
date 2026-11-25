import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("inventory", Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state/inventory.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)

class InventoryTests(unittest.TestCase):
    def test_add_missing_native_devices(self):
        configured = [{"name": "Office Left", "ip": "192.168.1.2"}]
        native = [{"name": "Renamed Office", "ip": "192.168.1.2"},
                  {"name": "Bar", "ip": "192.168.1.3"},
                  {"name": "Master bedroom", "ip": "192.168.1.4"}]
        self.assertEqual(inventory.merge_controller_inventory(configured, native),
                         configured + native[1:])

    def test_ambiguous_address_change_not_added(self):
        configured = [{"name": "Bar", "ip": "192.168.1.3"}]
        self.assertEqual(inventory.merge_controller_inventory(configured,
                         [{"name": "bar", "ip": "192.168.1.4"}]), configured)
