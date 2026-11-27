import ast
import importlib.util
import base64
import os
import asyncio
import time
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock

source=Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/light.py"
spec=importlib.util.spec_from_file_location("shutdown_parallel",source.with_name("parallel.py"))
parallel=importlib.util.module_from_spec(spec)
spec.loader.exec_module(parallel)
tree=ast.parse(source.read_text(encoding="utf-8"))
nodes=[n for n in tree.body if getattr(n,"name",None) in ("Group","update_together")]
ns={"asyncio":asyncio,"base64":base64,"os":os,"HomeAssistantError":RuntimeError,"_LOGGER":Mock(),"time":time,"WINDOW_CHASE":"Window Chase","HOUSE_CHASE":"House and Garden Chase","SLIDE_CHASE":"Bush and Window Chase V2","CHASE_EFFECTS":("Window Chase","House and Garden Chase","Bush and Window Chase V2")}
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),"exec"),ns)
Group=ns["Group"]
update=ns["update_together"]

def group(two=False):
    g=Group.__new__(Group)
    g._batch=parallel.ControllerBatch()
    g.members=[{"mac":"a","name":"A","role":"master" if two else "none"}]+([{"mac":"b","name":"B","role":"slave"}] if two else [])
    g.master=g.members[0]
    g.lock=asyncio.Lock()
    g.state={m["mac"]:(True,255,(255,0,0,0)) for m in g.members}
    g.effects={m["mac"]:"Solid" for m in g.members}
    g.patterns={m["mac"]:{} for m in g.members}
    g.pane_maps={"a":[[0],[1]]}
    g.pane_states={}
    g.pane_effects={}
    g.pane_patterns={}
    g.pane_geometry={}
    g.pane_overrides=set()
    g.combined_selected=set()
    g.combined_geometry={}
    g.chase_started=0.0
    g.chase_zones=[]
    g.house_zones=[]
    g.active=True
    g.error=False
    g.task=Mock()
    g.tokens={}
    g.expiry={}
    g.login=AsyncMock()
    g.mode=AsyncMock()
    g.member_mode=AsyncMock()
    g.black_frames=AsyncMock()
    g.send=AsyncMock()
    g.notify=Mock()
    return g

class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_pane_stops(self):
        g=group()
        task=g.task
        await g.update_pane(g.master,0,False,{})
        g.member_mode.assert_not_awaited()
        self.assertTrue(g.active)
        await g.update_pane(g.master,1,False,{})
        self.assertEqual(g.member_mode.await_count,len(g.members))
        for m in g.members:
            g.member_mode.assert_any_await(m,"off")
        task.cancel.assert_called_once()
        self.assertFalse(g.active)
        self.assertFalse(g.any_on())
        self.assertEqual(g.send.await_count,1)

    async def test_group_keeps_other_member_running(self):
        g=group(True)
        await update([(g,[g.members[0]])],False,{})
        self.assertTrue(g.active)
        g.member_mode.assert_not_awaited()
        await update([(g,[g.members[1]])],False,{})
        self.assertEqual(g.member_mode.await_count,len(g.members))
        for m in g.members:
            g.member_mode.assert_any_await(m,"off")
        self.assertFalse(g.active)
        g.black_frames.assert_awaited_once_with(1)
        self.assertEqual(g.login.await_count,2)

    async def test_off_failure_does_not_restart(self):
        g=group()
        task=g.task
        g.member_mode.side_effect=RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await update([(g,g.members)],False,{})
        self.assertFalse(g.active)
        self.assertTrue(g.error)
        task.cancel.assert_called_once()
        g.send.assert_not_awaited()
        g.member_mode.side_effect=None
        await update([(g,g.members)],False,{})
        self.assertFalse(g.error)

    async def test_combined_standalone_halves(self):
        a,b=group(),group()
        b.lock=a.lock
        await update([(a,a.members),(b,b.members)],False,{},combined=True)
        for g in (a,b):
            self.assertFalse(g.active)
            g.member_mode.assert_awaited_once_with(g.master,"off")
            g.black_frames.assert_not_awaited()

    async def test_bush_effects_independent(self):
        g=group()
        g.pane_patterns={("a",0):{"Static rainbow":b"abc"},("a",1):{"Rising spiral":None}}
        await g.update_pane(g.master,0,True,{"effect":"Static rainbow"})
        await g.update_pane(g.master,1,True,{"effect":"Rising spiral"})
        self.assertEqual(g.pane_effects,{("a",0):"Static rainbow",("a",1):"Rising spiral"})
        await g.update_pane(g.master,0,False,{})
        self.assertTrue(g.active)
        self.assertTrue(g.pane_states[("a",1)][0])
        await g.update_pane(g.master,1,False,{})
        self.assertFalse(g.active)

    async def test_window_chase_group_start_and_off(self):
        g=group(True)
        g.chase_zones=[("a",(0,)),("b",(0,))]
        await update([(g,g.members)],True,{"effect":"Window Chase","brightness":32})
        self.assertEqual(set(g.effects.values()),{"Window Chase"})
        self.assertGreater(g.chase_started,0)
        started=g.chase_started
        await update([(g,g.members)],True,{"brightness":64})
        self.assertEqual(g.chase_started,started)
        self.assertEqual({s[1] for s in g.state.values()},{64})
        await update([(g,g.members)],True,{"effect":"Solid"})
        self.assertEqual(set(g.effects.values()),{"Solid"})
        await update([(g,g.members)],False,{})
        self.assertFalse(g.active)

    async def test_authentication_retry_and_reuse(self):
        g=group()
        del g.login
        g.call=AsyncMock(side_effect=[RuntimeError("TimeoutError:"),{"authentication_token":"token","challenge-response":"proof"},{}])
        await g.ensure_login(g.master)
        self.assertEqual(g.tokens["a"],"token")
        self.assertEqual(g.call.await_count,3)
        self.assertNotEqual(g.call.await_args_list[0].kwargs["payload"],g.call.await_args_list[1].kwargs["payload"])
        await g.ensure_login(g.master)
        self.assertEqual(g.call.await_count,3)

    async def test_group_shutdown_reuses_tokens(self):
        g=group(True)
        g.tokens={m["mac"]:"token" for m in g.members}
        g.expiry={m["mac"]:time.monotonic()+600 for m in g.members}
        await update([(g,g.members)],False,{})
        g.login.assert_not_awaited()
        g.black_frames.assert_awaited_once_with(1)

    async def test_hold_black_between_cues(self):
        g=group()
        g.hold_idle=True
        await update([(g,g.members)],False,{})
        self.assertTrue(g.active)
        self.assertFalse(g.any_on())
        g.member_mode.assert_not_awaited()
        g.send.assert_awaited_once()
        await update([(g,g.members)],True,{"rgb_color":(0,255,0)})
        self.assertTrue(g.any_on())
        g.login.assert_not_awaited()
        g.hold_idle=False
        await update([(g,g.members)],False,{})
        self.assertFalse(g.active)
