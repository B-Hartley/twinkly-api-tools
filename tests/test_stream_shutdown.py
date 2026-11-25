import ast
import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock

source=Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/light.py"
tree=ast.parse(source.read_text(encoding="utf-8"))
nodes=[n for n in tree.body if getattr(n,"name",None) in ("Group","update_together")]
ns={"asyncio":asyncio}
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),"exec"),ns)
Group=ns["Group"]
update=ns["update_together"]

def group(two=False):
    g=Group.__new__(Group)
    g.members=[{"mac":"a","name":"A"}]+([{"mac":"b","name":"B"}] if two else [])
    g.master=g.members[0]
    g.lock=asyncio.Lock()
    g.state={m["mac"]:(True,255,(255,0,0,0)) for m in g.members}
    g.effects={m["mac"]:"Solid" for m in g.members}
    g.patterns={m["mac"]:{} for m in g.members}
    g.pane_maps={"a":[[0],[1]]}
    g.pane_states={}
    g.pane_overrides=set()
    g.combined_selected=set()
    g.active=True
    g.error=False
    g.task=Mock()
    g.login=AsyncMock()
    g.mode=AsyncMock()
    g.send=AsyncMock()
    g.notify=Mock()
    return g

class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_pane_stops(self):
        g=group()
        task=g.task
        await g.update_pane(g.master,0,False,{})
        g.mode.assert_not_awaited()
        self.assertTrue(g.active)
        await g.update_pane(g.master,1,False,{})
        g.mode.assert_awaited_once_with("off")
        task.cancel.assert_called_once()
        self.assertFalse(g.active)
        self.assertFalse(g.any_on())
        self.assertEqual(g.send.await_count,1)

    async def test_group_keeps_other_member_running(self):
        g=group(True)
        await update([(g,[g.members[0]])],False,{})
        self.assertTrue(g.active)
        g.mode.assert_not_awaited()
        await update([(g,[g.members[1]])],False,{})
        g.mode.assert_awaited_once_with("off")
        self.assertFalse(g.active)

    async def test_off_failure_does_not_restart(self):
        g=group()
        task=g.task
        g.mode.side_effect=RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await update([(g,g.members)],False,{})
        self.assertFalse(g.active)
        self.assertTrue(g.error)
        task.cancel.assert_called_once()
        g.send.assert_not_awaited()
        g.mode.side_effect=None
        await update([(g,g.members)],False,{})
        self.assertFalse(g.error)

    async def test_combined_standalone_halves(self):
        a,b=group(),group()
        b.lock=a.lock
        await update([(a,a.members),(b,b.members)],False,{},combined=True)
        for g in (a,b):
            self.assertFalse(g.active)
            g.mode.assert_awaited_once_with("off")
