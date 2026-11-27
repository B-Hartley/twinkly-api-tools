import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("front_parallel", Path(__file__).resolve().parents[1] / "Front_movies_V3" / "upload_movies.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

def members(n):
    return [{"name":str(i),"mac":str(i),"role":"slave"} for i in range(n)] + [{"name":"master","mac":"master","role":"master"}]

class ParallelTests(unittest.TestCase):
    def test_parallel_slaves_master_alone(self):
        lock=threading.Lock()
        gate=threading.Barrier(3)
        active=set()
        completed=set()
        def work(m,stop):
            name=m["name"]
            with lock:
                active.add(name)
                if name=="master":
                    self.assertEqual(active,{"master"})
                    self.assertEqual(completed,{"0","1","2","3"})
            if name in {"0","1","2"}:
                gate.wait(timeout=3)
            with lock:
                active.remove(name)
                completed.add(name)
            return "done"
        self.assertEqual(u.upload_group_movie(members(4),work,3,u.Pacing(False)),3)

    def test_communication_failure_reduces_next_wave_and_next_movie(self):
        p=u.Pacing(False)
        calls=[]
        def work(m,stop):
            calls.append((m["name"],threading.current_thread() is threading.main_thread()))
            if m["name"]=="0":
                p.backoff("192.168.1.2")
            return "done"
        self.assertEqual(u.upload_group_movie(members(5),work,3,p),1)
        self.assertEqual(dict(calls)["3"],True)
        self.assertEqual(dict(calls)["4"],True)
        self.assertEqual(calls[-1],("master",True))

    def test_deferred_revisited_before_master(self):
        calls=[]
        def work(m,stop):
            name=m["name"]
            calls.append(name)
            return "deferred" if name=="0" and calls.count(name)==1 else "done"
        self.assertEqual(u.upload_group_movie(members(3),work,1,u.Pacing(False)),1)
        self.assertEqual(calls,["0","1","2","0","master"])

    def test_fatal_failure_does_not_start_next_wave_or_master(self):
        calls=[]
        def work(m,stop):
            calls.append(m["name"])
            if m["name"]=="0":
                raise RuntimeError("ambiguous write")
            return "done"
        with self.assertRaisesRegex(RuntimeError,"ambiguous write"):
            u.upload_group_movie(members(5),work,3,u.Pacing(False))
        self.assertTrue(set(calls).issubset({"0","1","2"}))

    def test_preflight_reads_overlap_and_fallback(self):
        p=u.Pacing(False)
        gate=threading.Barrier(3)
        calls=[]
        def inspect(m,timeout):
            calls.append((m["name"],threading.current_thread() is threading.main_thread()))
            if m["name"] in {"0","1","2"}:
                gate.wait(timeout=3)
            if m["name"]=="0":
                p.backoff("192.168.1.2")
            return {"mode":"off"},"token"
        with patch.object(u,"inspect",side_effect=inspect):
            results,n=u.inspect_group(members(4),45,3,p)
        self.assertEqual(n,1)
        self.assertEqual(len(results),5)
        self.assertTrue(all(e is None for _,_,e in results))
        self.assertTrue(dict(calls)["3"])

    def test_journal_serialized(self):
        records={}
        with tempfile.TemporaryDirectory() as folder, patch.object(u,"JOURNAL",Path(folder)/"journal"):
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda i:u.record_stage(records,(str(i),),"verified"),range(30)))
            self.assertEqual(u.read_journal(),records)
            self.assertEqual(len(records),30)

    def test_pacing_preserved_across_threads(self):
        p=u.Pacing(False)
        with patch.object(u.time,"monotonic",return_value=10):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(p.finished,"movies/full","192.168.1.2").result()
        with patch.object(u.time,"monotonic",return_value=10.5), patch.object(u.time,"sleep") as sleep:
            p.wait("192.168.1.2","gestalt")
            sleep.assert_called_once_with(1.5)
