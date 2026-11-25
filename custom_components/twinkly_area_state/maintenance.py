"""Persistent opt-in isolation of Home Assistant's Twinkly clients."""
import asyncio
import base64
import os

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.components.persistent_notification import async_create
from .const import DOMAIN


class Maintenance:
    def __init__(self,hass,entry,ctx,query,session):
        self.hass=hass
        self.entry=entry
        self.ctx=ctx
        self.query=query
        self.session=session
        self.store=Store(hass,1,f"{DOMAIN}.maintenance.{entry.entry_id}")
        self.enabled=False
        self.owned=set()
        self.transition=asyncio.Lock()
        self.light_loaded=False

    def guard(self):
        if self.enabled:
            raise HomeAssistantError("Twinkly maintenance mode is on")

    async def save(self):
        await self.store.async_save({"enabled":self.enabled,"owned":sorted(self.owned)})

    async def load(self):
        data=await self.store.async_load() or {}
        self.enabled=bool(data.get("enabled",False))
        self.owned=set(data.get("owned",[]))
        if self.enabled:
            errors=await self.disable_native()
            if errors:
                self.report(errors)

    def report(self,errors):
        async_create(self.hass,
            ("Maintenance is ON. " if self.enabled else "Maintenance is OFF. ")+
            ("Isolation incomplete: "+"; ".join(errors) if errors else
             "Home Assistant Twinkly clients suspended." if self.enabled else
             "Native integrations and virtual discovery restored."),
            title="Twinkly maintenance mode",notification_id=DOMAIN+"_maintenance")

    async def disable_native(self):
        errors=[]
        manager=self.hass.config_entries
        for entry in manager.async_entries("twinkly"):
            if entry.disabled_by is not None and entry.entry_id not in self.owned:
                continue
            if entry.disabled_by is None:
                self.owned.add(entry.entry_id)
                await self.save()
            try:
                await manager.async_set_disabled_by(entry.entry_id,ConfigEntryDisabler.USER)
                if entry.state in (ConfigEntryState.LOADED,ConfigEntryState.SETUP_IN_PROGRESS):
                    raise RuntimeError("still running")
            except Exception as exc:
                errors.append(f"{entry.title}: native disable failed ({type(exc).__name__})")
        return errors

    async def physical_off(self):
        """Authenticate, read group role and send off to each master/standalone."""
        errors=[]
        for device in self.ctx["devices"]:
            ip=device["ip"]
            async def call(path,**kw):
                r=await self.query(self.session,ip,path,**kw)
                if not r.get("ok"):
                    raise RuntimeError(r.get("error","request failed"))
                return r["data"]
            try:
                login=await call("/xled/v1/login",method="POST",
                    payload={"challenge":base64.b64encode(os.urandom(32)).decode()})
                token=login["authentication_token"]
                await call("/xled/v1/verify",method="POST",token=token,
                    payload={"challenge-response":login["challenge-response"]})
                role=(await call("/xled/v1/application/group",token=token))["group"]["mode"]
                if role in ("master","none"):
                    await call("/xled/v1/led/mode",method="POST",token=token,payload={"mode":"off"})
                    if (await call("/xled/v1/led/mode",token=token)).get("mode")!="off":
                        raise RuntimeError("off not confirmed")
                elif role!="slave":
                    raise RuntimeError("unknown group role")
            except Exception as exc:
                errors.append(f"{device['name']}: off preparation failed ({type(exc).__name__})")
        return errors

    async def enable(self):
        async with self.transition:
            self.enabled=True
            await self.save()
            errors=[]
            try:
                await self.ctx["show"].stop()
            except Exception as exc:
                errors.append("show stop failed: "+type(exc).__name__)
            for group in self.ctx.get("virtual_groups",[]):
                try:
                    await group.stop()
                except Exception as exc:
                    errors.append("virtual stop failed: "+type(exc).__name__)
            # Remove virtual entities so commands cannot start new streams.
            if self.light_loaded:
                try:
                    if not await self.hass.config_entries.async_unload_platforms(self.entry,["light"]):
                        raise RuntimeError("platform unload failed")
                    self.light_loaded=False
                    self.ctx["virtual_groups"]=[]
                    self.ctx["combined_bushes"]=[]
                except Exception as exc:
                    errors.append("virtual unload failed: "+type(exc).__name__)
            async with self.ctx["lock"]:
                errors.extend(await self.disable_native())
                errors.extend(await self.physical_off())
            self.report(errors)
            if errors:
                raise HomeAssistantError("Maintenance enabled, but isolation/off not fully verified; see notification")

    async def disable(self):
        async with self.transition:
            errors=[]
            manager=self.hass.config_entries
            for entry_id in sorted(self.owned):
                native=manager.async_get_entry(entry_id)
                try:
                    if native and native.disabled_by==ConfigEntryDisabler.USER:
                        await manager.async_set_disabled_by(entry_id,None)
                        if native.disabled_by is not None:
                            raise RuntimeError("still disabled")
                    self.owned.discard(entry_id)
                    await self.save()
                except Exception as exc:
                    errors.append(f"{entry_id}: restore failed ({type(exc).__name__})")
            if errors:
                self.report(errors)
                raise HomeAssistantError("Native restoration incomplete")
            self.enabled=False
            await self.save()
            if not self.light_loaded:
                try:
                    await manager.async_forward_entry_setups(self.entry,["light"])
                    self.light_loaded=True
                except Exception as exc:
                    self.enabled=True
                    await self.save()
                    errors=await self.disable_native()
                    self.report(["Virtual discovery failed: "+type(exc).__name__]+errors)
                    raise HomeAssistantError("Virtual discovery failed; maintenance restored") from exc
            self.report([])
