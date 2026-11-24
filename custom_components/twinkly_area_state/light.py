"""Opt-in HTTP virtual lights for a verified local Twinkly group."""
import asyncio
import aiohttp
import time
import base64
import logging
import os

from homeassistant.components.light import LightEntity, ColorMode, LightEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
from .realtime import pixel_width, mapped_rainbow, spiral_geometry, spiral_frame, SPIRAL_EFFECTS, MOVING_SPIRALS

_LOGGER=logging.getLogger(__name__)
MODE="/xled/v1/led/mode"

class Group:
    def __init__(self,hass,members,lock,query):
        self.hass=hass
        self.members=members
        self.lock=lock
        self.query=query
        self.session=async_get_clientsession(hass)
        self.tokens={}
        self.entities=[]
        self.task=None
        self.active=False
        self.error=False
        self.previous=None
        self.expiry={}
        self.effects={m["mac"]:"Solid" for m in members}
        self.geometry={}
        self.patterns={m["mac"]:{} for m in members}
        self.master=next(m for m in members if m["role"]=="master")
        self.state={m["mac"]:(False,255,(255,255,255,0)) for m in members}

    async def call(self,m,path,**kw):
        r=await self.query(self.session,m["ip"],path,**kw)
        if not r["ok"]:
            raise HomeAssistantError(f"{m['name']}: {path}: {r.get('error')}")
        return r["data"]

    async def login(self,m):
        challenge=base64.b64encode(os.urandom(32)).decode()
        d=await self.call(m,"/xled/v1/login",method="POST",payload={"challenge":challenge})
        token=d["authentication_token"]
        await self.call(m,"/xled/v1/verify",method="POST",token=token,
                        payload={"challenge-response":d["challenge-response"]})
        self.tokens[m["mac"]]=token
        lifetime=d.get("authentication_token_expires_in",14400)
        self.expiry[m["mac"]]=time.monotonic()+max(1,float(lifetime)-30)

    async def validate(self):
        for m in self.members:
            g=await self.call(m,"/xled/v1/gestalt")
            if str(g.get("mac","")).lower().replace(":","").replace("-","")!=m["mac"] or g.get("number_of_led")!=m["led_count"] or pixel_width(g)!=m["width"]:
                raise HomeAssistantError("Controller identity or pixel format changed")
            await self.login(m)
            d=await self.call(m,"/xled/v1/application/group",token=self.tokens[m["mac"]])
            group=d["group"]
            if (str(group.get("uid","")).lower()!=m["uid"] or group.get("mode")!=m["role"] or group.get("size")!=sum(x["led_count"] for x in self.members) or (0 if group.get("offset") is None and m["role"]=="master" else group.get("offset"))!=m["offset"]):
                raise HomeAssistantError("Group changed; reload integration before streaming")

    def load_maps(self, data):
        archives=data.get("_area_manager",{})
        records=archives.get("groups",{})
        originals=archives.get("maps_3d",{})
        for m in self.members:
            patterns={}
            record=records.get(m["uid"],{})
            saved=next((x for x in record.get("members",[]) if x.get("mac")==m["mac"] and x.get("led_count")==m["led_count"] and x.get("offset")==m["offset"] and x.get("size")==m["size"] and x.get("role")==m["role"]),None)
            if saved:
                for name,axis,reverse in (("Static rainbow","y",False),("Static rainbow reversed","y",True),("Horizontal rainbow","x",False)):
                    try:
                        patterns[name]=mapped_rainbow(saved["layout"],m["led_count"],m["width"],axis,reverse)
                    except (ValueError,KeyError,TypeError) as exc:
                        _LOGGER.warning("%s: %s unavailable: %s",m["name"],name,exc)
            mac=m["mac"]
            self.geometry.pop(mac,None)
            original=originals.get(mac,{})
            if original.get("led_count")==m["led_count"]:
                try:
                    self.geometry[mac]=spiral_geometry(original["layout"],m["led_count"])
                    patterns.update({name:None for name in SPIRAL_EFFECTS})
                except (ValueError,KeyError,TypeError) as exc:
                    _LOGGER.warning("%s: spiral unavailable: %s",m["name"],exc)
            self.patterns[mac]=patterns
            if self.effects[m["mac"]] not in patterns:
                self.effects[m["mac"]]="Solid"

    def notify(self):
        for e in self.entities:
            e.async_write_ha_state()

    async def send(self):
        now=time.monotonic()
        results=await asyncio.gather(*(self.send_member(m,now) for m in self.members),return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):
                raise result

    async def send_member(self,m,now):
        if time.monotonic()>=self.expiry.get(m["mac"],0):
            await self.login(m)
        on,brightness,rgbw=self.state[m["mac"]]
        r,g,b,w=(round(c*brightness/255) if on else 0 for c in rgbw)
        pixel=bytes((w,r,g,b) if m["width"]==4 else (r,g,b))
        pattern=self.patterns[m["mac"]].get(self.effects[m["mac"]])
        frame=bytes(round(c*brightness/255) if on else 0 for c in pattern) if pattern is not None else pixel*m["led_count"]
        effect=self.effects[m["mac"]]
        if effect in SPIRAL_EFFECTS and m["mac"] in self.geometry:
            frame=spiral_frame(self.geometry[m["mac"]],m["width"],effect,now,rgbw,brightness if on else 0)
        for attempt in range(3):
            try:
                async with self.session.post(f"http://{m['ip']}/xled/v1/led/rt/frame",
                        data=frame,headers={"X-Auth-Token":self.tokens[m["mac"]],
                        "Content-Type":"application/octet-stream","Connection":"close"},
                        timeout=aiohttp.ClientTimeout(total=8,connect=4),allow_redirects=False) as resp:
                    from . import parse_twinkly_json
                    status=resp.status
                    data=parse_twinkly_json(await resp.read()) if status==200 else {}
                if status in (401,403) and attempt<2:
                    await self.login(m)
                    continue
                if status!=200 or data.get("code")!=1000:
                    raise HomeAssistantError(f"{m['name']}: frame rejected (HTTP {status}, code {data.get('code')})")
                break
            except (asyncio.TimeoutError,aiohttp.ClientConnectionError) as exc:
                if attempt==2:
                    raise HomeAssistantError(f"{m['name']}: frame connection failed after 3 attempts") from exc
                await asyncio.sleep(.5*(attempt+1))

    async def mode(self,value):
        await self.call(self.master,MODE,method="POST",token=self.tokens[self.master["mac"]],payload={"mode":value})

    async def restore(self):
        await self.login(self.master)
        await self.mode(self.previous or "off")

    async def update(self,m,on,kwargs):
        async with self.lock:
            old=self.state[m["mac"]]
            old_effect=self.effects[m["mac"]]
            effect=kwargs.get("effect", "Solid" if "rgbw_color" in kwargs or "rgb_color" in kwargs else old_effect)
            if effect not in ["Solid", *self.patterns[m["mac"]]]:
                raise HomeAssistantError("Effect requires a captured map for the current group")
            b=kwargs.get("brightness",old[1])
            c=kwargs.get("rgbw_color",kwargs.get("rgb_color",old[2]))
            c=tuple(c)+(0,) if len(c)==3 else tuple(c)
            if not self.active:
                await self.validate()
                self.previous=(await self.call(self.master,MODE,token=self.tokens[self.master["mac"]]))["mode"]
                if self.previous not in ("off","movie","playlist","color","effect"):
                    raise HomeAssistantError("Group already in unsupported mode; stop external streaming first")
                try:
                    await self.mode("rt")
                    self.effects[m["mac"]]=effect
                    self.state[m["mac"]]=(on,b,c)
                    await self.send()
                except Exception:
                    self.effects[m["mac"]]=old_effect
                    self.state[m["mac"]]=old
                    await self.restore()
                    raise
                self.active=True
                self.error=False
                self.task=self.hass.async_create_background_task(self.run(),"Twinkly virtual light frames")
            else:
                self.effects[m["mac"]]=effect
                self.state[m["mac"]]=(on,b,c)
                try:
                    await self.send()
                except Exception:
                    self.effects[m["mac"]]=old_effect
                    self.state[m["mac"]]=old
                    raise
            self.notify()

    async def run(self):
        failures=0
        while self.active:
            moving=any(self.state[k][0] and effect in MOVING_SPIRALS for k,effect in self.effects.items())
            await asyncio.sleep((.2 if moving else .75) if failures==0 else min(10,2**failures))
            async with self.lock:
                if not self.active:
                    return
                try:
                    if failures:
                        await self.validate()
                        await self.mode("rt")
                    await self.send()
                    failures=0
                    if self.error:
                        self.error=False
                        self.notify()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures+=1
                    _LOGGER.warning("Twinkly virtual frame failure %s: %s",failures,exc)
                    if failures>=3 and not self.error:
                        self.error=True
                        self.notify()

    async def stop(self,movie=False):
        async with self.lock:
            if not self.active and not self.error:
                return
            self.active=False
            if self.task:
                self.task.cancel()
                self.task=None
            await self.login(self.master)
            await self.mode("movie" if movie else self.previous or "off")
            self.error=False
            self.state={k:(False,v[1],v[2]) for k,v in self.state.items()}
            self.notify()

class VirtualLight(LightEntity):
    _attr_should_poll=False
    _attr_supported_features=LightEntityFeature.EFFECT
    def __init__(self,group,m):
        self.group=group
        self.member=m
        self._attr_name="Twinkly "+m["name"]+" Virtual"
        self._attr_unique_id=m["mac"]+"_virtual"
        self._attr_color_mode=ColorMode.RGBW if m["width"]==4 else ColorMode.RGB
        self._attr_supported_color_modes={self._attr_color_mode}
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,m["mac"])},name=m["name"]+" Virtual",manufacturer="Twinkly")
    @property
    def effect_list(self):
        return ["Solid", *self.group.patterns[self.member["mac"]]]
    @property
    def effect(self):
        return self.group.effects[self.member["mac"]]
    @property
    def available(self):
        return not self.group.error
    @property
    def is_on(self):
        return self.group.active and self.group.state[self.member["mac"]][0]
    @property
    def brightness(self):
        return self.group.state[self.member["mac"]][1]
    @property
    def rgbw_color(self):
        return self.group.state[self.member["mac"]][2]
    @property
    def rgb_color(self):
        return self.rgbw_color[:3]
    async def async_turn_on(self,**kwargs):
        await self.group.update(self.member,True,kwargs)
    async def async_turn_off(self,**kwargs):
        if self.group.active:
            await self.group.update(self.member,False,{})

async def async_setup_entry(hass,entry,async_add_entities):
    from . import query
    from .realtime import basic_group_members
    ctx=hass.data[DOMAIN][entry.entry_id]
    selected=ctx["devices"]
    session=async_get_clientsession(hass)
    async def read(m,path,**kw):
        reply=await query(session,m["ip"],path,**kw)
        if not reply["ok"]:
            raise ValueError(f"{m['name']}: {path}: {reply.get('error')}")
        return reply["data"]
    groups=[]
    async with ctx["lock"]:
        observations={}
        for m in selected:
            try:
                gestalt=await read(m,"/xled/v1/gestalt")
                login=await read(m,"/xled/v1/login",method="POST",
                    payload={"challenge":base64.b64encode(os.urandom(32)).decode()})
                token=login["authentication_token"]
                await read(m,"/xled/v1/verify",method="POST",token=token,
                    payload={"challenge-response":login["challenge-response"]})
                group=(await read(m,"/xled/v1/application/group",token=token))["group"]
                if group.get("mode") in ("master","slave"):
                    observations.setdefault(str(group.get("uid","")).lower(),[]).append((m,gestalt,group))
            except (ValueError,KeyError,TypeError, OSError) as exc:
                _LOGGER.warning("%s: virtual light discovery skipped: %s",m["name"],exc)
        data=await ctx["store"].async_load() or {}
        for uid,items in observations.items():
            try:
                members=basic_group_members(items)
                group=Group(hass,members,ctx["lock"],query)
                group.load_maps(data)
                group.entities=[VirtualLight(group,m) for m in members]
                groups.append(group)
            except (ValueError,KeyError,TypeError,StopIteration) as exc:
                _LOGGER.warning("Virtual group %s not created: %s",uid,exc)
    ctx["virtual_groups"]=groups
    async_add_entities([e for group in groups for e in group.entities])
