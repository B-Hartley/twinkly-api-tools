import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

src=Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/mapping_mode.py"
tree=ast.parse(src.read_text(encoding="utf-8"))
node=next(n for n in tree.body if isinstance(n,ast.ClassDef))
ns=dict(asyncio=asyncio,ConfigEntryDisabler=SimpleNamespace(USER="user"),
        ConfigEntryState=SimpleNamespace(LOADED="loaded",SETUP_IN_PROGRESS="setup",FAILED_UNLOAD="failed"),
        HomeAssistantError=RuntimeError,configured_twinkly_devices=lambda entries: [])
exec(compile(ast.Module(body=[node],type_ignores=[]),str(src),"exec"),ns)
MappingMode=ns["MappingMode"]

class MappingTests(unittest.IsolatedAsyncioTestCase):
    def build(self):
        a=SimpleNamespace(entry_id="a",title="A",disabled_by=None,state="loaded")
        b=SimpleNamespace(entry_id="b",title="B",disabled_by="user",state="not_loaded")
        async def change(eid,value):
            e={"a":a,"b":b}[eid]
            e.disabled_by=value
            e.state="not_loaded" if value else "loaded"
            return True
        entries=SimpleNamespace(async_entries=lambda domain:[a,b],
            async_get_entry=lambda eid:{"a":a,"b":b}.get(eid),
            async_set_disabled_by=AsyncMock(side_effect=change))
        g=Mock()
        ctx={"lock":asyncio.Lock(),"devices":[{"name":"A","ip":"192.168.1.5"}],"virtual_groups":[g]}
        store=SimpleNamespace(async_load=AsyncMock(return_value={}),async_save=AsyncMock())
        mode=MappingMode(SimpleNamespace(config_entries=entries),ctx,store,None,None)
        mode.off=AsyncMock()
        return mode,a,b,g,entries

    async def test_enable_disable(self):
        mode,a,b,g,entries=self.build()
        await mode.enable()
        self.assertTrue(mode.enabled)
        self.assertEqual(mode.owned,["a"])
        g.suspend_local.assert_called_once()
        mode.off.assert_awaited_once()
        with self.assertRaises(RuntimeError):
            mode.guard()
        await mode.disable()
        self.assertFalse(mode.enabled)
        self.assertIsNone(a.disabled_by)
        self.assertEqual(b.disabled_by,"user")
        self.assertEqual(entries.async_set_disabled_by.await_count,2)

    async def test_off_error_stays_suspended(self):
        mode,a,b,g,entries=self.build()
        mode.off.side_effect=RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await mode.enable()
        self.assertTrue(mode.enabled)
        self.assertTrue(mode.issues)
        self.assertEqual(a.disabled_by,"user")

    async def test_restart_load(self):
        mode,a,b,g,entries=self.build()
        mode.store.async_load.return_value={"enabled":True,"owned":["a"],"issues":[]}
        await mode.load()
        with self.assertRaises(RuntimeError):
            mode.guard()
        self.assertEqual(mode.owned,["a"])
