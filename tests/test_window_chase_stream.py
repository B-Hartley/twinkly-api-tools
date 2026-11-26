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
        self.assertEqual(self.zones[-1],("5",(0,1)))
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
        self.assertEqual(wc.chase_frame(m,self.zones,11.1*wc.PANE_SECONDS,255,(255,0,0,0)),bytes([0,255,0,0]*2+[0]*8))
        self.assertEqual(wc.chase_frame(m,self.zones,12.1*wc.PANE_SECONDS,255,(255,0,0,0)),bytes([0]*8+[0,255,0,0]*2))
        self.assertEqual(wc.chase_frame(self.members[0],self.zones,22.1*wc.PANE_SECONDS,255,(255,0,0,0)),bytes([255,0,0]*2+[0]*6))

    def test_house_route(self):
        names=["front left top","front left bottom","back left bush","light tree","front right bush","mid right","back bush top","back bush bottom","icicles"]
        extra=[dict(name=n,mac=n,led_count=4,width=3) for n in names]
        maps={**self.maps,"front right bush":[[0,1],[2,3]]}
        zones=wc.house_zones(self.members+extra,maps)
        self.assertEqual(len(zones),20)
        self.assertEqual({mac for mac,_ in zones[0]},{"front left top","front left bottom"})
        for m in extra[:2]:
            self.assertEqual(wc.chase_frame(m,zones,0.1*wc.PANE_SECONDS,255,(255,0,0,0),loop=True),bytes([255,0,0]*4))
        self.assertEqual(zones[2],(("front right bush",(0,1)),))
        self.assertEqual(zones[3],(("front right bush",(2,3)),))
        self.assertEqual(wc.chase_frame(self.members[0],zones,7.1*wc.PANE_SECONDS,255,(255,0,0,0),loop=True),bytes([255,0,0]*2+[0]*6))
        self.assertEqual(wc.house_zones(self.members+extra[:-1],maps),[])
        extra[2]["name"]="Backl Bush"
        self.assertEqual(wc.house_zones(self.members+extra,maps),zones)


class SlideChaseTests(unittest.TestCase):
    def setUp(self):
        names=["front left top","front left bottom","front right bush","mid right",
               "back left bush","back bush top","back bush bottom","light tree","icicles",*wc.SLIDE_WINDOWS]
        self.members=[dict(name=n,mac=n,led_count=4,width=4 if i%2 else 3) for i,n in enumerate(names)]
        self.panes={n:[[0,1],[2,3]] for n in ("front right bush",*wc.SLIDE_WINDOWS)}
        self.screen={}
        self.originals={}
        for i,n in enumerate(names):
            coords=[dict(x=i*3+j*.4,y=(j%2)*.1) for j in range(4)]
            self.screen[n]=dict(source="2d",coordinates=coords)
            self.originals[n]=dict(source="3d",coordinates=[dict(x=j,y=j%2,z=(j%3)*.2) for j in range(4)])
        self.stops=wc.slide_geometry(self.members,self.panes,self.originals,self.screen)

    def test_route_combined_bushes_and_lounge_handoff(self):
        self.assertEqual(len(self.stops),20)
        self.assertEqual(set(self.stops[0]["pixels"]),{"front left top","front left bottom"})
        self.assertEqual(set(self.stops[1]["pixels"]),{"light tree"})
        self.assertEqual(set(self.stops[2]["pixels"]),{"front right bush"})
        self.assertEqual(set(self.stops[5]["pixels"]),{"back bush top","back bush bottom"})
        self.assertEqual(set(self.stops[6]["pixels"]),{"back left bush"})
        self.assertEqual(set(self.stops[7]["pixels"]),{"lounge"})
        self.assertEqual(set(self.stops[3]["pixels"]),{"front right bush"})
        self.assertEqual(set(self.stops[11]["pixels"]),{"icicles"})

    def test_whole_zone_and_spatial_transfer(self):
        first=self.members[0]
        full=wc.slide_frame(first,self.stops,0,255,(255,180,80,0))
        self.assertTrue(all(any(full[i:i+3]) for i in range(0,12,3)))
        t=self.stops[0]["hold"]+self.stops[0]["transfer"]*.5
        middle=wc.slide_frame(first,self.stops,t,255,(255,180,80,0))
        self.assertGreater(len(set(middle[i:i+3] for i in range(0,12,3))),1)
        end=wc.slide_duration(self.stops)
        self.assertEqual(full,wc.slide_frame(first,self.stops,end,255,(255,180,80,0)))
        self.assertEqual(wc.slide_frame(first,self.stops,t,0,(255,180,80,0)),bytes(12))

    def test_missing_3d_rejected(self):
        del self.originals["back bush bottom"]
        with self.assertRaises(ValueError):
            wc.slide_geometry(self.members,self.panes,self.originals,self.screen)
