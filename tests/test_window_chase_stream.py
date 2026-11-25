import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("window_chase", Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state/window_chase.py")
wc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wc)

class WindowChaseTests(unittest.TestCase):
    def setUp(self):
        self.members = [dict(name=name, mac=str(i), led_count=4, width=4 if i%2 else 3) for i,name in enumerate(wc.WINDOW_ORDER)]
        self.maps = {m["mac"]: [[0,1],[2,3]] for m in self.members}
        self.zones = wc.chase_zones(self.members, self.maps)

    def test_complete_and_ordered(self):
        self.assertEqual(len(self.zones),12)
        self.assertEqual(self.zones[0],("0",(0,1)))
        self.assertEqual(self.zones[-1],("5",(2,3)))
        self.assertEqual(wc.chase_zones(self.members[:-1],self.maps),[])
        self.maps["0"]=[[0,1],[1,3]]
        self.assertEqual(wc.chase_zones(self.members,self.maps),[])

    def test_frames_and_brightness(self):
        m=self.members[0]
        self.assertEqual(wc.chase_frame(m,self.zones,0,128,(255,0,0,0)),bytes([128,0,0]*2+[0]*6))
        self.assertEqual(wc.chase_frame(m,self.zones,.33,255,(255,0,0,0)),bytes([0]*6+[255,0,0]*2))
        self.assertEqual(wc.chase_frame(m,self.zones,.65,255,(255,0,0,0)),bytes(12))
        self.assertEqual(wc.chase_frame(m,self.zones,0,0,(255,0,0,0)),bytes(12))

    def test_rgbw_and_bush_dark(self):
        m=self.members[1]
        self.assertEqual(wc.chase_frame(m,self.zones,.65,255,(1,2,3,4)),bytes([4,1,2,3]*2+[0]*8))
        bush=dict(mac="bush",led_count=600,width=4)
        self.assertEqual(wc.chase_frame(bush,self.zones,0,255,(255,255,255,255)),bytes(2400))

    def test_reverse_and_wrap(self):
        m=self.members[-1]
        self.assertEqual(wc.chase_frame(m,self.zones,11.1*wc.PANE_SECONDS,255,(255,0,0,0)),bytes([0]*8+[0,255,0,0]*2))
        self.assertEqual(wc.chase_frame(m,self.zones,12.1*wc.PANE_SECONDS,255,(255,0,0,0)),bytes([0,255,0,0]*2+[0]*8))
        self.assertEqual(wc.chase_frame(self.members[0],self.zones,22.1*wc.PANE_SECONDS,255,(255,0,0,0)),bytes([255,0,0]*2+[0]*6))
