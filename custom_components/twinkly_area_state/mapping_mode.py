"""Mapping mode: stop HA traffic, without changing groups or layouts."""
import asyncio
import base64
import os

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from .config_flow import configured_twinkly_devices


class MappingMode:
    def __init__(self,hass,ctx,store,query,session):
        self.hass=hass
        self.ctx=ctx
        self.store=store
        self.query=query
        self.session=session
        self.enabled=False
        self.owned=[]
        self.issues=[]
        self.transition=asyncio.Lock()

    async def load(self):
        data=await self.store.async_load() or {}
        self.enabled=bool(data.get("enabled"))
        self.owned=list(data.get("owned",[]))
        self.issues=list(data.get("issues",[]))

    async def save(self):
        await self.store.async_save(dict(enabled=self.enabled,owned=self.owned,issues=self.issues))

    def guard(self):
        if self.enabled:
            raise HomeAssistantError("Twinkly mapping mode is on; controller communication suspended")

    async def suspend_native(self):
        entries=self.hass.config_entries
        for e in entries.async_entries("twinkly"):
            try:
                if e.disabled_by is None:
                    if e.entry_id not in self.owned:
                        self.owned.append(e.entry_id)
                        await self.save()
                    await entries.async_set_disabled_by(e.entry_id,ConfigEntryDisabler.USER)
                if e.state in (ConfigEntryState.LOADED,ConfigEntryState.SETUP_IN_PROGRESS):
                    raise RuntimeError("native integration has not unloaded; restart may be required")
            except Exception as exc:
                self.issues.append(f"{e.title}: native suspension: {exc}")
        await self.save()

    async def off(self,device):
        async def checked(path,**kw):
            r=await self.query(self.session,device["ip"],path,**kw)
            if not r["ok"]:
                raise RuntimeError(r.get("error","request rejected"))
            return r["data"]
        challenge=base64.b64encode(os.urandom(32)).decode()
        login=await checked("/xled/v1/login",method="POST",payload={"challenge":challenge})
        token=login["authentication_token"]
        await checked("/xled/v1/verify",method="POST",token=token,payload={"challenge-response":login["challenge-response"]})
        await checked("/xled/v1/led/mode",method="POST",token=token,payload={"mode":"off"})
        mode=await checked("/xled/v1/led/mode",token=token)
        if mode.get("mode")!="off":
            raise RuntimeError("physical off not verified")

    async def enable(self):
        async with self.transition:
            if self.enabled:
                return
            async with self.ctx["lock"]:
                self.enabled=True
                self.issues=[]
                await self.save()
                for group in self.ctx.get("virtual_groups",[]):
                    group.suspend_local()
                devices={d["ip"]:d for d in self.ctx["devices"]}
                for d in configured_twinkly_devices(self.hass.config_entries.async_entries("twinkly")):
                    devices.setdefault(d["ip"],d)
                await self.suspend_native()
                # Last permitted local traffic: authenticated physical off.
                for device in devices.values():
                    try:
                        await self.off(device)
                    except Exception as exc:
                        self.issues.append(f"{device['name']}: off: {exc}")
                await self.save()
            if self.issues:
                raise HomeAssistantError("Mapping mode enabled, but not fully verified: "+"; ".join(self.issues))

    async def disable(self):
        async with self.transition:
            entries=self.hass.config_entries
            issues=[]
            for eid in self.owned.copy():
                try:
                    e=entries.async_get_entry(eid)
                    if e is not None and e.disabled_by==ConfigEntryDisabler.USER:
                        await entries.async_set_disabled_by(eid,None)
                    self.owned.remove(eid)
                    await self.save()
                except Exception as exc:
                    issues.append(str(exc))
            if issues:
                self.issues=issues
                await self.save()
                raise HomeAssistantError("Could not restore native Twinkly: "+"; ".join(issues))
            self.enabled=False
            self.issues=[]
            await self.save()
