"""Offline tests for bounded Home Assistant controller transactions."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

ROOT=Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state"
spec=importlib.util.spec_from_file_location("ha_parallel",ROOT/"parallel.py")
parallel=importlib.util.module_from_spec(spec)
spec.loader.exec_module(parallel)
tree=ast.parse((ROOT/"light.py").read_text())
ns={"asyncio":asyncio,"HomeAssistantError":RuntimeError}
exec(compile(ast.Module(body=[n for n in tree.body if getattr(n,"name","")=="Group"],type_ignores=[]),str(ROOT/"light.py"),"exec"),ns)
Group=ns["Group"]

def devices(n):
    return [{"mac":str(i),"ip":str(i),"name":str(i),"role":"master" if i==0 else "slave"} for i in range(n)]

class ControllerBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_at_once_and_order_preserved(self):
        batch=parallel.ControllerBatch()
        active=0
        peak=0
        async def work(m):
            nonlocal active,peak
            active+=1
            peak=max(active,peak)
            await asyncio.sleep(.001*(6-int(m["mac"])))
            active-=1
            return m["mac"]
        self.assertEqual(await batch.run(devices(6),work),list(map(str,range(6))))
        self.assertEqual(peak,3)

    async def test_limit_shared_between_groups_and_same_controller_serial(self):
        batch=parallel.ControllerBatch()
        active=set()
        peak=0
        async def work(m):
            nonlocal peak
            key=m["ip"]
            self.assertNotIn(key,active)
            active.add(key)
            peak=max(peak,len(active))
            await asyncio.sleep(.002)
            active.remove(key)
            return key
        await asyncio.gather(batch.run(devices(5),work),batch.run(devices(5),work))
        self.assertEqual(peak,3)

    async def test_fallback_does_not_replay_failed_operation(self):
        batch=parallel.ControllerBatch()
        calls=[]
        active=0
        async def work(m):
            nonlocal active
            active+=1
            calls.append((m["mac"],active))
            try:
                await asyncio.sleep(.001)
                if m["mac"]=="0":
                    raise RuntimeError("HTTP 401")
                if int(m["mac"])>=3:
                    self.assertEqual(active,1)
                return m["mac"]
            finally:
                active-=1
        result=await batch.run(devices(6),work,return_exceptions=True)
        self.assertIsInstance(result[0],RuntimeError)
        self.assertEqual(batch.limit,1)
        self.assertEqual(sorted(k for k,_ in calls),list(map(str,range(6))))
        self.assertEqual(active,0)

    async def test_read_result_failure_falls_back(self):
        batch=parallel.ControllerBatch()
        async def work(m):
            return {"ok":m["mac"]!="0"}
        results=await batch.run(devices(5),work,failed_result=lambda r:not r["ok"])
        self.assertEqual(len(results),5)
        self.assertEqual(batch.limit,1)

    def group(self,n=6):
        group=Group.__new__(Group)
        group.members=devices(n)
        group.master=group.members[0]
        group._batch=parallel.ControllerBatch()
        group.tokens={m["mac"]:"token" for m in group.members}
        return group

    async def test_movie_selection_master_after_all_slaves(self):
        group=self.group()
        active=set()
        done=set()
        async def call(m,path,**kwargs):
            key=m["mac"]
            active.add(key)
            if key=="0":
                self.assertEqual(active,{"0"})
                self.assertEqual(done,{"1","2","3","4","5"})
            self.assertEqual(kwargs["payload"],{"id":int(key)+20})
            await asyncio.sleep(.001)
            active.remove(key)
            done.add(key)
        group.call=call
        paths={m["mac"]:"/movies/current" for m in group.members}
        selection={k:int(k)+20 for k in paths}
        await group.select_movie(paths,selection)
        self.assertEqual(len(done),6)

    async def test_failed_selection_drains_wave_and_never_selects_master(self):
        group=self.group()
        calls=[]
        finished=[]
        async def call(m,path,**kwargs):
            key=m["mac"]
            calls.append(key)
            if key=="1":
                raise RuntimeError("HTTP 503")
            await asyncio.sleep(.002)
            finished.append(key)
        group.call=call
        paths={m["mac"]:"/movies/current" for m in group.members}
        with self.assertRaisesRegex(RuntimeError,"HTTP 503"):
            await group.select_movie(paths,{k:int(k) for k in paths})
        self.assertTrue(set(calls)<= {"1","2","3"})
        self.assertEqual(set(calls)-{"1"},set(finished))
        self.assertEqual(group._batch.limit,1)

    async def test_bad_movie_metadata_no_writes(self):
        group=self.group()
        group.call=AsyncMock()
        with self.assertRaises(RuntimeError):
            await group.select_movie({"0":"/current"},{"0":1})
        group.call.assert_not_awaited()

    def test_unsupported_endpoint_not_network_failure(self):
        self.assertFalse(parallel.communication_error("HTTP 404"))
        self.assertTrue(parallel.communication_error("HTTP 401"))
        self.assertTrue(parallel.communication_error("ClientPayloadError"))
