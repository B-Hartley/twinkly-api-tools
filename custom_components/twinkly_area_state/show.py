"""Run local, trusted JSON choreography; never start automatically."""
import asyncio
import json
import logging
from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

_LOGGER=logging.getLogger(__name__)


class Show:
    def __init__(self,hass,ctx):
        self.hass=hass
        self.ctx=ctx
        self.task=None

    def guard(self):
        if self.ctx.get("mapping"):
            self.ctx["mapping"].guard()

    async def call(self,domain,service,entity,**data):
        await self.hass.services.async_call(domain,service,
            {"entity_id":entity,**data},blocking=True)

    async def start(self,call):
        self.guard()
        if self.task and not self.task.done():
            raise HomeAssistantError("Christmas show already running")
        plan=await self.hass.async_add_executor_job(
            lambda: json.loads(Path(__file__).with_name("christmas_show.json").read_text(encoding="utf-8")))
        # Resolve logical show keys to actual registry IDs, including existing
        # installations whose IDs were retained or renamed by the user.
        by_key={}
        for group in self.ctx.get("virtual_groups",[]):
            for entity in group.entities:
                key=getattr(entity,"show_key",None)
                if key and entity.entity_id:
                    if key in by_key and by_key[key]!=entity.entity_id:
                        raise HomeAssistantError("Ambiguous show light: "+key)
                    by_key[key]=entity.entity_id
        unknown=[key for key in plan["targets"] if key not in by_key]
        if unknown:
            raise HomeAssistantError("Show lights not registered: "+", ".join(unknown))
        for section in plan["sections"]:
            for cue in section["cues"]:
                cue["targets"]=[by_key[key] for key in cue["targets"]]
        plan["targets"]=[by_key[key] for key in plan["targets"]]
        missing=[e for e in plan["targets"] if not self.hass.states.get(e) or
                 self.hass.states.get(e).state in ("unavailable","unknown")]
        if missing:
            raise HomeAssistantError("Show lights unavailable: "+", ".join(missing))
        self.guard()
        if self.task and not self.task.done():
            raise HomeAssistantError("Christmas show already running")
        self.task=self.hass.async_create_background_task(
            self.run(plan,call.data),"Christmas.exe")

    async def stop(self,call=None):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)

    async def run(self,plan,options):
        speaker=plan["speaker"]
        state=self.hass.states.get(speaker)
        audio=options.get("audio",True) and bool(options.get("audio_base")) and state is not None
        audio=audio and state.state in ("idle","standby","off")
        # Never interrupt a playing speaker or an existing Sonos group.
        audio=audio and len(state.attributes.get("group_members") or [speaker])<=1
        volume=state.attributes.get("volume_level") if audio else None
        owned=False
        loop=asyncio.get_running_loop()
        try:
            if audio:
                try:
                    await self.call("media_player","volume_set",speaker,volume_level=options.get("volume",.05))
                    owned=True
                except Exception:
                    audio=False
            for section in plan["sections"]:
                self.guard()
                if audio:
                    try:
                        await self.call("media_player","play_media",speaker,
                            media_content_id=options["audio_base"].rstrip("/")+"/"+section["id"]+".mp3",
                            media_content_type="music")
                        deadline=loop.time()+6
                        while loop.time()<deadline:
                            st=self.hass.states.get(speaker)
                            if st and st.state=="playing":
                                break
                            await asyncio.sleep(.1)
                        else:
                            raise RuntimeError("Sonos did not report playing")
                    except Exception as exc:
                        _LOGGER.warning("Show audio unavailable; continuing silently: %s",exc)
                        audio=False
                start=loop.time()+ (options.get("offset_ms",0)/1000 if audio else 0)
                for cue in sorted(section["cues"],key=lambda c:c["at"]):
                    await asyncio.sleep(max(0,start+cue["at"]-loop.time()))
                    self.guard()
                    if cue.get("off") or options.get("brightness_pct",2)==0:
                        await self.call("light","turn_off",cue["targets"])
                    else:
                        await self.call("light","turn_on",cue["targets"],
                            rgb_color=cue["rgb"],brightness=max(1,round(cue["brightness"]*options.get("brightness_pct",2)/100)))
                await asyncio.sleep(max(0,start+section["duration"]-loop.time()))
        except asyncio.CancelledError:
            try:
                self.guard()
                await self.call("light","turn_off",plan["targets"])
            except Exception:
                _LOGGER.exception("Show cancellation: lights could not be switched off")
            raise
        except Exception:
            _LOGGER.exception("Christmas show failed")
            try:
                self.guard()
                await self.call("light","turn_off",plan["targets"])
            except Exception:
                _LOGGER.exception("Show cleanup failed")
        finally:
            if owned:
                try:
                    await self.call("media_player","media_stop",speaker)
                    if volume is not None:
                        await self.call("media_player","volume_set",speaker,volume_level=volume)
                except Exception:
                    _LOGGER.exception("Sonos cleanup failed")
