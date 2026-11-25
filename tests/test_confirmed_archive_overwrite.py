import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "archive_import", Path(__file__).resolve().parents[1] /
    "custom_components/twinkly_area_state/archive_import.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def record(x):
    layout = {"source": "3d", "synthesized": False,
              "coordinates": [{"x": x, "y": 0, "z": 0}]}
    return {"name": "Tree", "led_count": 1, "layout": layout,
            "sha256": module.digest(layout)}

class ArchiveOverwriteTests(unittest.TestCase):
    def test_default_preserves(self):
        old, new = record(0), record(1)
        merged, _, _, skipped = module.merge_archives({"a": old}, {"a": new}, {"a": 1})
        self.assertEqual(merged["a"], old)
        self.assertTrue(skipped)

    def test_overwrite(self):
        old, new = record(0), record(1)
        saved = {"a": old}
        merged, _, _, skipped = module.merge_archives(saved, {"a": new}, {"a": 1}, overwrite=True)
        self.assertEqual(merged["a"], new)
        self.assertEqual(saved["a"], old)
        self.assertFalse(skipped)

    def test_offline_opt_in(self):
        new = record(1)
        self.assertFalse(module.merge_archives({}, {"a": new}, {})[0])
        self.assertEqual(module.merge_archives({}, {"a": new}, {}, allow_unverified=True)[0]["a"], new)

    def test_known_count_mismatch_rejected(self):
        self.assertFalse(module.merge_archives({}, {"a": record(1)}, {"a": 2},
            overwrite=True, allow_unverified=True)[0])

if __name__ == "__main__":
    unittest.main()
