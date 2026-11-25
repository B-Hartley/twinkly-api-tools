"""Bounded, opt-in playback diagnostics. No cloud or mapping writes."""
import asyncio
import time
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.persistent_notification import async_create
from .const import DOMAIN

TRANSPORTS=("auto","http-per-controller","udp-per-controller",
            "http-master-combined","udp-master-combined")
SETTINGS={
    vol.Optional("transport",default="auto"):vol.In(TRANSPORTS),
    vol.Optional("fps",default=10):vol.All(vol.Coerce(int),vol.Range(min=1,max=25)),
    vol.Optional("packet_spacing_ms",default=0):vol.All(vol.Coerce(float),vol.Range(min=0,max=20)),
    vol.Optional("brightness_limit_pct",default=100):vol.All(vol.Coerce(int),vol.Range(min=1,max=100)),
}

async def stop_playback_test(ctx):
    task=ctx.get("playback_test")
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)

def register_playback_actions(hass,ctx):
    def groups():
        return ctx.get("virtual_groups",[])
    def idle():
        if any(g.active or g.error for g in groups()):
            raise HomeAssistantError("Switch off virtual lights before changing playback")
        show=ctx.get("show")
        if show and show.task and not show.task.done():
            raise HomeAssistantError("Stop Christmas show first")
        task=ctx.get("playback_test")
        if task and not task.done():
            raise HomeAssistantError("Playback test already running")
        if not groups():
            raise HomeAssistantError("No verified virtual groups")
    async def configure(call):
        async with ctx["lock"]:
            idle()
            for g in groups():
                g.playback=dict(call.data)
        async_create(hass,str(dict(call.data))+" (until integration reload)",
                     title="Twinkly playback configured",notification_id=DOMAIN+"_playback")
    async def test(call):
        async with ctx["lock"]:
            idle()
            chosen=[g for g in groups() if any(m["name"].casefold()==call.data["controller"].casefold() for m in g.members)]
            if len(chosen)!=1:
                raise HomeAssistantError("Enter an exact discovered controller name")
            g=chosen[0]
            ctx["playback_test"]=hass.async_create_background_task(run_test(hass,ctx,g,dict(call.data)),"Twinkly playback diagnostic")
    async def stop(call):
        await stop_playback_test(ctx)
    hass.services.async_register(DOMAIN,"configure_playback",configure,schema=vol.Schema(SETTINGS))
    hass.services.async_register(DOMAIN,"test_playback",test,schema=vol.Schema({
        **{k:v for k,v in SETTINGS.items() if k.schema!="transport"},
        vol.Optional("transport",default="udp-per-controller"):vol.In(TRANSPORTS),
        vol.Required("controller"):str,
        vol.Optional("duration",default=10):vol.All(vol.Coerce(int),vol.Range(min=2,max=60)),
        vol.Optional("brightness_pct",default=2):vol.All(vol.Coerce(int),vol.Range(min=1,max=10)),
        vol.Optional("pattern",default="isolate"):vol.In(("isolate","sequence","chase","colour")),
    }))
    hass.services.async_register(DOMAIN,"stop_playback_test",stop)

def diagnostic_frames(members, target, pattern, elapsed, brightness):
    ordered=sorted(members,key=lambda m:m["offset"])
    colours=((255,0,0),(0,255,0),(0,0,255))
    frames={}
    for i,m in enumerate(ordered):
        active=(m["name"].casefold()==target.casefold()) if pattern in ("isolate","colour") else i==int(elapsed//2)%len(ordered)
        rgb=colours[int(elapsed//2)%3] if pattern=="colour" else colours[i%3]
        pixel=bytes(round(c*brightness/100) if active else 0 for c in ((0,)+rgb if m["width"]==4 else rgb))
        if pattern=="chase" and active:
            pos=int(elapsed*30)%m["led_count"]
            dark=bytes(m["width"])
            frame=b"".join(pixel if (j-pos)%m["led_count"]<10 else dark for j in range(m["led_count"]))
        else:
            frame=pixel*m["led_count"]
        frames[m["mac"]]=frame
    return frames

async def run_test(hass,ctx,g,opts):
    count=0
    error=None
    old=g.playback
    entered=False
    started=None
    elapsed=0
    async with ctx["lock"]:
        try:
            if any(x.active or x.error for x in ctx.get("virtual_groups",[])):
                raise HomeAssistantError("Virtual playback started before diagnostic")
            if ctx.get("mapping"):
                ctx["mapping"].guard()
            g.playback={k:opts[k] for k in ("transport","fps","packet_spacing_ms","brightness_limit_pct")}
            if g.playback["transport"].endswith("master-combined") and len({m["width"] for m in g.members})!=1:
                raise HomeAssistantError("Combined transport requires uniform RGB/RGBW format")
            await g.prepare_start()
            entered=True
            await g.mode("rt")
            started=time.monotonic()
            while time.monotonic()-started<opts["duration"]:
                if ctx.get("mapping"):
                    ctx["mapping"].guard()
                t=time.monotonic()-started
                frames=diagnostic_frames(g.members,opts["controller"],opts["pattern"],t,
                    min(opts["brightness_pct"],opts["brightness_limit_pct"]))
                await g.send_frames(frames)
                count+=1
                await asyncio.sleep(max(0,started+count/opts["fps"]-time.monotonic()))
        except asyncio.CancelledError:
            error="Stopped by user"
            raise
        except Exception as exc:
            error=str(exc)
        finally:
            elapsed=time.monotonic()-started if started is not None else 0
            g.playback=old
            if entered:
                try:
                    await g.login(g.master)
                    await g.mode("off")
                except Exception as exc:
                    error=(error or "")+"; OFF failed: "+str(exc)
            async_create(hass,
                f"Transport: {opts['transport']}; pattern: {opts['pattern']}; frames submitted: {count}; "
                f"elapsed: {elapsed:.2f}s; achieved submission FPS: {count/elapsed if elapsed else 0:.2f}. "
                + (f"Error: {error}" if error else "No transport exceptions.")
                +" UDP submission is not confirmation of physical playback. Observe the lights.",
                title="Twinkly playback diagnostic",notification_id=DOMAIN+"_playback_test")
