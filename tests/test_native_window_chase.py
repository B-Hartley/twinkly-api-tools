import unittest
import window_chase as wc

class NativeChaseTests(unittest.TestCase):
    def records(self):
        names=list(wc.ORDER)+["front left top"]
        sizes=[210]*6+[600]
        total=sum(sizes)
        offset=0
        result=[]
        for i,(name,count) in enumerate(zip(names,sizes)):
            result.append(dict(name=name,mac=str(i),ip=str(i),count=count,width=4,
                group=dict(uid="front",mode="master" if i==0 else "slave",size=total,offset=offset),
                catalogue=dict(movies=[],available_frames=100,max_capacity=100),mode="off"))
            offset+=count
        return result

    def test_complete_group_movie(self):
        jobs=wc.build(self.records(),uid="test")
        self.assertEqual(len(jobs),7)
        self.assertEqual({m["unique_id"] for _,m,_ in jobs},{"test"})
        self.assertEqual({m["frames_number"] for _,m,_ in jobs},{96})
        for r,m,data in jobs:
            self.assertEqual(len(data),96*r["count"]*4)
        self.assertFalse(any(jobs[-1][2]))
        for i in range(96):
            active=0
            for r,m,data in jobs:
                size=r["count"]*4
                active+=bool(any(data[i*size:(i+1)*size]))
            self.assertEqual(active,1)

    def test_incomplete_group(self):
        with self.assertRaises(ValueError):
            wc.build(self.records()[:-1])

    def test_insufficient_capacity(self):
        records=self.records()
        records[-1]["catalogue"]["available_frames"]=95
        with self.assertRaises(ValueError):
            wc.build(records)
