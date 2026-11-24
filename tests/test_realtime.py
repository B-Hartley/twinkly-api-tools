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

class DiagnosticTests(unittest.TestCase):
    members=[{"name":name,"offset":i*3,"leds":3,
              "area_layout":{"coordinates":[{"y":y} for y in (0,.25,.75)]}}
             for i,name in enumerate(("Office Left","Office Right"))]
    def test_isolation(self):
        self.assertEqual(rt.diagnostic_frame(self.members,0),bytes([255,0,0])*3+bytes(9))
        self.assertEqual(rt.diagnostic_frame(self.members,10),bytes(9)+bytes([0,255,0])*3)
    def test_opposite_spatial_phase(self):
        frame=rt.diagnostic_frame(self.members,20)
        flipped=rt.diagnostic_frame(self.members,20,invert=True)
        self.assertEqual(frame[:9],flipped[9:])
        self.assertEqual(frame[9:],flipped[:9])
        self.assertNotEqual(frame[3:6],frame[12:15])
    def test_invalid_axis(self):
        with self.assertRaises(KeyError):
            rt.diagnostic_frame(self.members,20,axis="z")


class MappedRainbowTests(unittest.TestCase):
    layout={"source":"2d","coordinates":[{"x":1,"y":0},{"x":0,"y":1},{"x":.5,"y":.5}]}
    def test_wrgb_and_reverse(self):
        frame=rt.mapped_rainbow(self.layout,3,4)
        self.assertEqual(frame[:4],bytes([0,255,0,0]))
        self.assertEqual(frame[4:8],bytes([0,255,0,255]))
        self.assertEqual(rt.mapped_rainbow(self.layout,3,4,reverse=True)[:4],frame[4:8])
        self.assertEqual(len(frame),12)
    def test_horizontal(self):
        self.assertEqual(rt.mapped_rainbow(self.layout,3,3,"x")[:3],bytes([255,0,255]))
    def test_bad_map(self):
        with self.assertRaises(ValueError):
            rt.mapped_rainbow(self.layout,4,4)
        with self.assertRaises(ValueError):
            rt.mapped_rainbow({"source":"2d","coordinates":[{"y":0}]*3},3,4)


class SpiralTests(unittest.TestCase):
    layout={"source":"3d","coordinates":[{"x":1,"y":0,"z":0},{"x":0,"y":1,"z":1},{"x":-1,"y":.5,"z":0},{"x":0,"y":.5,"z":-1}]}
    def test_loop_and_brightness(self):
        geo=rt.spiral_geometry(self.layout,4)
        args=(geo,4,"Rising spiral")
        frame=rt.spiral_frame(*args,0,(255,0,0,0),255)
        self.assertEqual(len(frame),16)
        self.assertEqual(frame,rt.spiral_frame(*args,6,(255,0,0,0),255))
        self.assertNotEqual(frame,rt.spiral_frame(*args,1.5,(255,0,0,0),255))
        self.assertEqual(rt.spiral_frame(*args,0,(255,0,0,0),0),bytes(16))
        self.assertEqual(frame[0::4],bytes(4))
    def test_static(self):
        geo=rt.spiral_geometry(self.layout,4)
        self.assertEqual(rt.spiral_frame(geo,3,"Static spiral",0,(0,255,0,0),255),rt.spiral_frame(geo,3,"Static spiral",2,(0,255,0,0),255))
    def test_reject_2d(self):
        with self.assertRaises(ValueError):
            rt.spiral_geometry(dict(self.layout,source="2d"),4)
    def test_group_three(self):
        uid="12345678-1234-1234-1234-123456789abc"
        obs=[({"name":str(i),"ip":str(i)},{"mac":f"{i:012x}","number_of_led":2,"led_profile":"RGB"},{"uid":uid,"mode":"master" if i==0 else "slave","offset":i*2,"size":6}) for i in range(3)]
        self.assertEqual(len(rt.basic_group_members(obs)),3)
        with self.assertRaises(ValueError):
            rt.basic_group_members(obs[:2])
