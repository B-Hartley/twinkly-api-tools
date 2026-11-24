"""Offline regression checks for master/slave LED offset validation."""
import ast
from pathlib import Path
import re
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state" / "__init__.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
          and node.name == "_capture_local_groups")
namespace = {"re": re}
exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
             str(SOURCE), "exec"), namespace)


def controller(item):
    return (item["mac"], item["led_count"], item["layout"], item["group"])


namespace["_validated_controller"] = controller
capture = namespace["_capture_local_groups"]
UID = "5a4acd14-f436-477a-ad2d-d7d4453aa1ac"


def sample(slave_offset=210, master_offset=None):
    master_group = {"mode": "master", "uid": UID, "size": 420}
    if master_offset is not None:
        master_group["offset"] = master_offset
    return {"devices": [
        {"status": "captured", "name": "Office left", "mac": "a" * 12,
         "led_count": 210, "layout": {"source": "2d"},
         "group": master_group, "responses": {}},
        {"status": "captured", "name": "Office right", "mac": "b" * 12,
         "led_count": 210, "layout": {"source": "2d"},
         "group": {"mode": "slave", "uid": UID, "size": 420,
                   "offset": slave_offset}, "responses": {}},
    ]}


class GroupOffsetTests(unittest.TestCase):
    def test_missing_master_offset_is_zero(self):
        members = capture(sample())[UID]["members"]
        self.assertEqual([(m["role"], m["offset"]) for m in members],
                         [("master", 0), ("slave", 210)])

    def test_explicit_master_offset_zero(self):
        self.assertEqual(len(capture(sample(master_offset=0))), 1)

    def test_missing_slave_offset_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing or invalid offset"):
            capture(sample(slave_offset=None))

    def test_overlapping_slave_offset_rejected(self):
        with self.assertRaisesRegex(ValueError, "inconsistent offsets"):
            capture(sample(slave_offset=0))

    def test_gap_in_offsets_rejected(self):
        with self.assertRaisesRegex(ValueError, "inconsistent offsets"):
            capture(sample(slave_offset=211))


if __name__ == "__main__":
    unittest.main()
