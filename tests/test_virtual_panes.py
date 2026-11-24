import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("panes", Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state/panes.py")
panes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panes)

class TestPanes(unittest.TestCase):
    def test_default_count(self):
        self.assertEqual(sum(v for k,v in panes.DEFAULTS.items() if k != "front right"), 24)

    def test_strands(self):
        parts = panes.strand_indices(210, (2,3,3,2))
        self.assertEqual([len(p) for p in parts], [42,63,63,42])
        self.assertEqual(sum(parts, []), list(range(210)))
        self.assertEqual(panes.strand_indices(210, (2,3,3,2), True), list(reversed(parts)))
        self.assertEqual([len(p) for p in panes.strand_indices(600,(1,1))], [300,300])
        with self.assertRaises(ValueError):
            panes.strand_indices(211,(2,3,3,2))

    def test_partition(self):
        layout = {"coordinates": [{"x": x} for x in (0, 0.2, 0.4, 0.6, 0.8, 1)]}
        self.assertEqual(panes.split_indices(layout, 6, 3), [[0, 1], [2, 3], [4, 5]])
        self.assertEqual(panes.split_indices(layout, 6, 3, reverse=True), [[4, 5], [2, 3], [0, 1]])

    def test_empty_zone(self):
        with self.assertRaises(ValueError):
            panes.split_indices({"coordinates": [{"x": 0}, {"x": 1}]}, 2, 3)

    def test_rgbw_composite(self):
        frame = bytearray(12)
        panes.composite(frame, 4, [1], bytes([1, 2, 3, 4]) * 3)
        self.assertEqual(frame, bytes(4) + bytes([1, 2, 3, 4]) + bytes(4))

if __name__ == "__main__":
    unittest.main()
