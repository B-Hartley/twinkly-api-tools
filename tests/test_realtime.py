import base64
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("rt",Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/realtime.py")
rt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

class RealtimeTests(unittest.TestCase):
    members=[{"offset":0,"leds":210},{"offset":210,"leds":210}]
    def test_blackout(self):
        self.assertEqual(rt.frame_at(self.members,0),bytes(1260))
    def test_left_only(self):
        frame=rt.frame_at(self.members,3,"solid")
        self.assertEqual(frame[:630],bytes([255,208,128])*210)
        self.assertEqual(frame[630:],bytes(630))
    def test_both(self):
        frame=rt.frame_at(self.members,5,"solid")
        self.assertEqual(frame,bytes([255,208,128])*420)
    def test_packets(self):
        token=base64.b64encode(b"12345678")
        frame=rt.frame_at(self.members,5)
        parts=rt.packets(token,frame)
        self.assertEqual(len(parts),2)
        self.assertEqual(parts[0][:12],b"\x0312345678\x00\x00\x00")
        self.assertEqual(parts[1][11],1)
        self.assertEqual(b"".join(p[12:] for p in parts),frame)
    def test_gap(self):
        with self.assertRaises(ValueError):
            rt.frame_at([{"offset":1,"leds":210}],5)
    def test_rgbw(self):
        self.assertEqual(rt.frame_at(self.members,5,"solid",4),bytes([0,255,208,128])*420)
