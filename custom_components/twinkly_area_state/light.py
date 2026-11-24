"""Opt-in HTTP virtual lights for a verified local Twinkly group."""
import asyncio
import base64
import logging
import os

from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN
from .realtime import pixel_width

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

    async def validate(self):
        for m in self.members:
            g=await self.call(m,"/xled/v1/gestalt")
            if str(g.get("mac","")).lower().replace(":","").replace("-","")!=m["mac"] or g.get("number_of_led")!=m["led_count"] or pixel_width(g)!=m["width"]:
                raise HomeAssistantError("Controller identity or pixel format changed")
            await self.login(m)
            d=await self.call(m,"/xled/v1/application/group",token=self.tokens[m["mac"]])
            group=d["group"]
            if (str(group.get("uid","")).lower()!=m["uid"] or group.get("mode")!=m["role"] or group.get("size")!=sum(x["led_count"] for x in self.members) or group.get("offset",0)!=m["offset"]):
                raise HomeAssistantError("Group changed; reload integration before streaming")

    def notify(self):
        for e in self.entities:
            e.async_write_ha_state()

    async def send(self):
        for m in self.members:
            on,brightness,rgbw=self.state[m["mac"]]
            r,g,b,w=(round(c*brightness/255) if on else 0 for c in rgbw)
            pixel=bytes((w,r,g,b) if m["width"]==4 else (r,g,b))
            async with self.session.post(f"http://{m['ip']}/xled/v1/led/rt/frame",
                    data=pixel*m["led_count"],headers={"X-Auth-Token":self.tokens[m["mac"]],
                    "Content-Type":"application/octet-stream"},timeout=3,allow_redirects=False) as resp:
                from . import parse_twinkly_json
                data=parse_twinkly_json(await resp.read())
                if resp.status!=200 or data.get("code")!=1000:
                    raise HomeAssistantError(f"{m['name']}: frame rejected")

    async def mode(self,value):
        await self.call(self.master,MODE,method="POST",token=self.tokens[self.master["mac"]],payload={"mode":value})

    async def restore(self):
        await self.login(self.master)
        await self.mode(self.previous or "off")

    async def update(self,m,on,kwargs):
        async with self.lock:
            old=self.state[m["mac"]]
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
                    self.state[m["mac"]]=(on,b,c)
                    await self.send()
                except Exception:
                    self.state[m["mac"]]=old
                    await self.restore()
                    raise
                self.active=True
                self.error=False
                self.task=self.hass.async_create_background_task(self.run(),"Twinkly virtual light frames")
            else:
                self.state[m["mac"]]=(on,b,c)
                try:
                    await self.send()
                except Exception:
                    self.state[m["mac"]]=old
                    raise
            self.notify()

    async def run(self):
        try:
            while self.active:
                await asyncio.sleep(.25)
                async with self.lock:
                    if self.active:
                        await self.send()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Twinkly virtual streaming failed")
            async with self.lock:
                self.active=False
                self.error=True
                try:
                    await self.restore()
                except Exception:
                    _LOGGER.exception("Twinkly mode restoration failed")
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
    def __init__(self,group,m):
        self.group=group
        self.member=m
        self._attr_name="Twinkly "+m["name"]+" Virtual"
        self._attr_unique_id=m["mac"]+"_virtual"
        self._attr_color_mode=ColorMode.RGBW if m["width"]==4 else ColorMode.RGB
        self._attr_supported_color_modes={self._attr_color_mode}
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,m["mac"])},name=m["name"]+" Virtual",manufacturer="Twinkly")
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
    from . import capture,query,_capture_local_groups
    ctx=hass.data[DOMAIN][entry.entry_id]
    devices=ctx["devices"]
    groups=[]
    # Initial release intentionally limited to the tested Office pair.
    selected=[d for d in devices if d["name"].casefold() in ("office left","office right")]
    if len(selected)!=2:
        return
    async with ctx["lock"]:
        live=[await capture(async_get_clientsession(hass),d) for d in selected]
        try:
            records=_capture_local_groups({"devices":live})
            for uid,record in records.items():
                members=record["members"]
                if any(m["size"]!=record["led_count"] for m in members):
                    raise ValueError("Inconsistent group size")
                for m in members:
                    item=next(d for d in live if d["ip"]==m["ip"])
                    m["width"]=pixel_width(item["responses"]["gestalt"]["data"])
                    m["uid"]=uid
                group=Group(hass,members,ctx["lock"],query)
                group.entities=[VirtualLight(group,m) for m in members]
                groups.append(group)
        except (ValueError,KeyError,StopIteration):
            _LOGGER.warning("Office virtual lights not created: complete verified local group required")
            return
    ctx["virtual_groups"]=groups
    async_add_entities([e for g in groups for e in g.entities])
