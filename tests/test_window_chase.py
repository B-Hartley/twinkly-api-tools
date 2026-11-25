import unittest
from window_chase import render, capacity, plan, ORDER

class WindowChaseTests(unittest.TestCase):
    def test_all_windows(self):
        for name in ORDER:
            rgb=render(name,210,3,16)
            wrgb=render(name,210,4,16)
            self.assertEqual(len(wrgb),210*4*16)
            self.assertEqual(wrgb[::4],bytes(210*16))
            self.assertEqual(rgb,bytes(v for i,v in enumerate(wrgb) if i%4))
            self.assertNotEqual(rgb[:630],rgb[630:1260])

    def test_capacity(self):
        record={"catalogue":{"movies":[],"available_frames":159}}
        with self.assertRaises(ValueError):
            capacity(record,160)
        record["catalogue"]["available_frames"]=160
        capacity(record,160)
        record["catalogue"]["movies"]=[{}]*16
        with self.assertRaises(ValueError):
            capacity(record,160)

    def test_common_movie_id(self):
        records=[dict(name=n,mac=n,count=210,width=4,catalogue={"movies":[],"available_frames":160}) for n in ORDER]
        jobs=plan(records,16,20)
        self.assertEqual(len({m["unique_id"] for _,m,_ in jobs}),1)

if __name__=="__main__":
    unittest.main()
