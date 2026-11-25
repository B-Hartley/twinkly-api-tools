import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("realtime",Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/realtime.py")
rt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

class TestVerticalRainbow(unittest.TestCase):
    def test_rgbw_and_direction(self):
        for source in ("2d","3d"):
            layout={"source":source,"coordinates":[{"y":1},{"y":0},{"y":0.5}]}
            frame=rt.vertical_rainbow(layout,3,4)
            self.assertEqual(frame[:4],bytes([0,255,0,0]))
            self.assertEqual(frame[4:8],bytes([0,255,0,255]))
            self.assertTrue(all(max(frame[i:i+4])==255 for i in range(0,12,4)))
            self.assertEqual(rt.vertical_rainbow(layout,3,3),bytes(frame[i] for i in range(12) if i%4))

    def test_bad_map(self):
        with self.assertRaises(ValueError):
            rt.vertical_rainbow({"source":"3d","coordinates":[{"y":0},{"y":0}]},2,3)

if __name__=="__main__":
    unittest.main()
