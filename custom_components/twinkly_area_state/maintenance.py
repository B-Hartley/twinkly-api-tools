"""Persistent communications isolation, with cleanup before wire lock."""
import asyncio
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.components.persistent_notification import async_create
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from .const import DOMAIN

class Maintenance:
    def __init__(self, hass, entry, ctx):
        self.hass, self.entry, self.ctx = hass, entry, ctx
        self.store = Store(hass, 1, f"{DOMAIN}.maintenance.{entry.entry_id}")
        self.enabled = False
        self.draining = False
        self.light_loaded = False
        self.owned = set()
        self.transition = asyncio.Lock()
        self.errors = []

    def guard(self):
        if self.enabled:
            raise HomeAssistantError("Twinkly maintenance mode is on")

    def guard_wire(self):
        if self.enabled and not self.draining:
            raise HomeAssistantError("Twinkly controller communications suspended")

    async def save(self):
        await self.store.async_save({"enabled": self.enabled, "owned": sorted(self.owned)})

    def report(self):
        async_create(self.hass,
            ("Maintenance ON: Area Manager communications suspended." if self.enabled else
             "Maintenance OFF: Area Manager communications restored.") +
            (" Warnings: " + "; ".join(self.errors) if self.errors else ""),
            title="Twinkly maintenance mode", notification_id=DOMAIN+"_maintenance")

    async def disable_native(self):
        manager = self.hass.config_entries
        for native in manager.async_entries("twinkly"):
            if native.source == "ignore":
                continue
            if native.disabled_by is not None and native.entry_id not in self.owned:
                continue
            try:
                self.owned.add(native.entry_id)
                await self.save()
                await manager.async_set_disabled_by(native.entry_id, ConfigEntryDisabler.USER)
                if native.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS):
                    raise RuntimeError("still loaded")
            except Exception as exc:
                self.errors.append(f"{native.title}: native disable failed ({type(exc).__name__})")

    async def load(self):
        data = await self.store.async_load() or {}
        self.enabled = bool(data.get("enabled", False))
        self.owned = set(data.get("owned", []))
        if self.enabled:
            await self.disable_native()
            self.report()

    async def enable(self):
        async with self.transition:
            self.errors = []
            self.enabled = True
            self.draining = True
            try:
                await self.save()
                try:
                    await self.ctx["show"].stop()
                except Exception as exc:
                    self.errors.append("Show stop: " + str(exc))
                async with self.ctx["lock"]:
                    groups = list(self.ctx.get("virtual_groups", []))
                    for group in groups:
                        group.active = False
                        group.hold_idle = False
                        if group.task:
                            group.task.cancel()
                            await asyncio.gather(group.task, return_exceptions=True)
                            group.task = None
                        try:
                            await group.validate()
                            await group.mode("off")
                        except Exception as exc:
                            self.errors.append("Controller off: " + str(exc))
                        finally:
                            group.native_effect = None
                            group.error = False
                            group.tokens.clear()
                            group.expiry.clear()
                    self.draining = False
                if self.light_loaded:
                    try:
                        if not await self.hass.config_entries.async_unload_platforms(self.entry, ["light"]):
                            raise RuntimeError("light unload failed")
                        self.light_loaded = False
                        self.ctx["virtual_groups"] = []
                        self.ctx["combined_bushes"] = []
                    except Exception as exc:
                        self.errors.append("Virtual lights: " + str(exc))
                await self.disable_native()
            finally:
                self.draining = False
                self.report()
            if self.errors:
                raise HomeAssistantError("Area Manager locked; see maintenance notification for isolation/off warnings")

    async def disable(self):
        async with self.transition:
            self.errors = []
            manager = self.hass.config_entries
            for entry_id in sorted(self.owned):
                try:
                    native = manager.async_get_entry(entry_id)
                    if native and native.disabled_by == ConfigEntryDisabler.USER:
                        await manager.async_set_disabled_by(entry_id, None)
                        if native.disabled_by is not None:
                            raise RuntimeError("still disabled")
                    self.owned.discard(entry_id)
                    await self.save()
                except Exception as exc:
                    self.errors.append("Native restore: " + str(exc))
            if self.errors:
                self.report()
                raise HomeAssistantError("Native restore incomplete")
            self.enabled = False
            try:
                await self.save()
                if not self.light_loaded:
                    await manager.async_forward_entry_setups(self.entry, ["light"])
                    self.light_loaded = True
            except Exception:
                self.enabled = True
                await self.save()
                await self.disable_native()
                self.report()
                raise
            self.report()
