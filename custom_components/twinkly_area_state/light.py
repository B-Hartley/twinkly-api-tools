"""Opt-in HTTP virtual lights for a verified local Twinkly group."""
import asyncio
import aiohttp
import time
import base64
import logging
import os
import socket

from homeassistant.components.light import LightEntity, ColorMode, LightEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from .const import DOMAIN
from .panes import DEFAULTS, pane_indices, pane_label, composite
from .realtime import packets, pixel_width, mapped_rainbow, spiral_geometry, spiral_frame, SPIRAL_EFFECTS, MOVING_SPIRALS, combined_bush_map

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
        self.pane_maps={}
        self.pane_states={}
        self.pane_effects={}
        self.pane_patterns={}
        self.pane_geometry={}
        self.pane_overrides=set()
        self.combined_geometry={}
        self.combined_selected=set()
        self.patterns={m["mac"]:{} for m in members}
        self.master=next(m for m in members if m["role"] in ("master","none"))
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

    async def validate_member(self,m):
        g=await self.call(m,"/xled/v1/gestalt")
        if str(g.get("mac","")).lower().replace(":","").replace("-","")!=m["mac"] or g.get("number_of_led")!=m["led_count"] or pixel_width(g)!=m["width"]:
            raise HomeAssistantError("Controller identity or pixel format changed")
        await self.login(m)
        d=await self.call(m,"/xled/v1/application/group",token=self.tokens[m["mac"]])
        group=d["group"]
        if m["role"]=="none":
            if group.get("mode")!="none":
                raise HomeAssistantError("Standalone controller joined a group; reload integration")
            return
        if (str(group.get("uid","")).lower()!=m["uid"] or group.get("mode")!=m["role"] or group.get("size")!=sum(x["led_count"] for x in self.members) or (0 if group.get("offset") is None and m["role"]=="master" else group.get("offset"))!=m["offset"]):
            raise HomeAssistantError("Group changed; reload integration before streaming")

    async def validate(self):
        await self.all_members(self.validate_member)

    async def all_members(self, callback):
        results=await asyncio.gather(*(callback(m) for m in self.members),return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):
                raise result

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
            name=" ".join(m["name"].casefold().split())
            if name in DEFAULTS:
                config=data.get("_virtual_panes",{}).get(mac,{})
                try:
                    self.pane_maps[mac]=pane_indices(name,m["led_count"],config.get("reverse"))
                except (ValueError,KeyError,TypeError) as exc:
                    self.pane_maps.pop(mac,None)
                    _LOGGER.warning("%s: pane map unavailable: %s",m["name"],exc)
            if name in ("front right","front right bush"):
                for i,indices in enumerate(self.pane_maps.get(mac,[])):
                    key=(mac,i)
                    zone={}
                    self.pane_geometry.pop(key,None)
                    if saved:
                        for label,axis,reverse in (("Static rainbow","y",False),("Static rainbow reversed","y",True),("Horizontal rainbow","x",False)):
                            try:
                                layout=dict(saved["layout"],coordinates=[saved["layout"]["coordinates"][j] for j in indices])
                                zone[label]=mapped_rainbow(layout,len(indices),m["width"],axis,reverse)
                            except (ValueError,KeyError,TypeError,IndexError):
                                pass
                    if original.get("led_count")==m["led_count"]:
                        try:
                            layout=dict(original["layout"],coordinates=[original["layout"]["coordinates"][j] for j in indices])
                            self.pane_geometry[key]=spiral_geometry(layout,len(indices))
                            zone.update({label:None for label in SPIRAL_EFFECTS})
                        except (ValueError,KeyError,TypeError,IndexError):
                            pass
                    self.pane_patterns[key]=zone
                    if self.pane_effects.get(key) not in zone:
                        self.pane_effects[key]="Solid"
            self.patterns[mac]=patterns
            if self.effects[m["mac"]] not in patterns:
                self.effects[m["mac"]]="Solid"

    def notify(self):
        for e in self.entities:
            e.async_write_ha_state()

    def member_on(self, mac):
        if mac in self.pane_overrides:
            return any(self.pane_states[(mac,i)][0]
                       for i in range(len(self.pane_maps.get(mac,[]))))
        return self.state[mac][0]

    def any_on(self):
        return any(self.member_on(m["mac"]) for m in self.members)

    async def finish_idle(self):
        """Called under lock after committing the last off request."""
        self.active=False
        if self.task:
            self.task.cancel()
            self.task=None
        self.state={k:(False,v[1],v[2]) for k,v in self.state.items()}
        self.pane_overrides.clear()
        self.pane_states.clear()
        try:
            await self.shutdown("off")
            self.error=False
        except Exception:
            # Never restart streaming just because the physical off failed.
            self.error=True
            raise
        finally:
            self.notify()

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
            geometry=self.combined_geometry.get(m["mac"],self.geometry[m["mac"]]) if m["mac"] in self.combined_selected else self.geometry[m["mac"]]
            frame=spiral_frame(geometry,m["width"],effect,now,rgbw,brightness if on else 0)
        mac=m["mac"]
        if mac in self.pane_overrides:
            frame=bytearray(frame)
            for i,indices in enumerate(self.pane_maps.get(mac,[])):
                enabled,level,color=self.pane_states[(mac,i)]
                r,g,b,w=(round(c*level/255) if enabled else 0 for c in color)
                pixel=bytes((w,r,g,b) if m["width"]==4 else (r,g,b))
                key=(mac,i)
                effect=self.pane_effects.get(key,"Solid")
                pattern=self.pane_patterns.get(key,{}).get(effect)
                if effect in SPIRAL_EFFECTS and key in self.pane_geometry:
                    zone=spiral_frame(self.pane_geometry[key],m["width"],effect,now,color,level if enabled else 0)
                elif pattern is not None:
                    zone=bytes(round(c*level/255) if enabled else 0 for c in pattern)
                else:
                    zone=pixel*len(indices)
                for k,j in enumerate(indices):
                    frame[j*m["width"]:(j+1)*m["width"]]=zone[k*m["width"]:(k+1)*m["width"]]
            frame=bytes(frame)
        if m["role"] != "none":
            # Grouped firmware may reject local-sized HTTP frames. Use the
            # native segmented real-time transport to each member instead.
            datagrams = packets(self.tokens[m["mac"]], frame)
            loop = asyncio.get_running_loop()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setblocking(False)
                for datagram in datagrams:
                    await loop.sock_sendto(sock, datagram, (m["ip"], 7777))
            return
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

    async def member_mode(self,m,value):
        if time.monotonic()>=self.expiry.get(m["mac"],0):
            await self.login(m)
        try:
            await self.call(m,MODE,method="POST",token=self.tokens[m["mac"]],payload={"mode":value})
        except HomeAssistantError as exc:
            if "HTTP 401" not in str(exc) and "HTTP 403" not in str(exc):
                raise
            await self.login(m)
            await self.call(m,MODE,method="POST",token=self.tokens[m["mac"]],payload={"mode":value})

    async def black_frames(self,duration):
        if self.master["role"]=="none":
            return
        loop=asyncio.get_running_loop()
        end=loop.time()+duration
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            while loop.time()<end:
                start=loop.time()
                for m in self.members:
                    for packet in packets(self.tokens[m["mac"]],bytes(m["width"]*m["led_count"])):
                        await loop.sock_sendto(sock,packet,(m["ip"],7777))
                await asyncio.sleep(max(0,start+1/15-loop.time()))

    async def mode(self,value):
        await self.all_members(lambda m:self.member_mode(m,value))
        if value=="rt":
            await self.black_frames(2)

    async def shutdown(self,value):
        try:
            if self.master["role"]!="none":
                await self.all_members(self.login)
                await self.black_frames(1)
        finally:
            await self.all_members(lambda m:self.member_mode(m,value))

    async def restore(self):
        await self.shutdown(self.previous or "off")

    def prepare(self,members,on,kwargs):
        """Compute all requested changes before sending any frame."""
        changes={}
        for m in members:
            key=m["mac"]
            old=self.state[key]
            effect=kwargs.get("effect", "Solid" if "rgbw_color" in kwargs or "rgb_color" in kwargs else self.effects[key])
            if effect not in ["Solid", *self.patterns[key]]:
                raise HomeAssistantError(f"{m['name']}: effect requires a captured map")
            b=kwargs.get("brightness",old[1])
            c=kwargs.get("rgbw_color",kwargs.get("rgb_color",old[2]))
            c=tuple(c)+(0,) if len(c)==3 else tuple(c)
            changes[key]=((on,b,c),effect)
        return changes

    async def prepare_start(self):
        await self.validate()
        self.previous=(await self.call(self.master,MODE,token=self.tokens[self.master["mac"]]))["mode"]
        if self.previous not in ("off","movie","playlist","color","effect"):
            raise HomeAssistantError("Group already in unsupported mode; stop external streaming first")

    async def update(self,m,on,kwargs):
        await update_together([(self,[m])],on,kwargs)

    async def update_pane(self,m,index,on,kwargs):
        mac=m["mac"]
        async with self.lock:
            if mac not in self.pane_maps:
                raise HomeAssistantError("Capture a valid pane map first")
            if not on and not self.active:
                if self.error:
                    await self.finish_idle()
                return
            starting=not self.active
            if starting:
                await self.prepare_start()
            old_states=self.pane_states.copy()
            old_overrides=self.pane_overrides.copy()
            old_effects=self.pane_effects.copy()
            try:
                if mac not in self.pane_overrides:
                    for i in range(len(self.pane_maps[mac])):
                        self.pane_states[(mac,i)]=self.state[mac]
                        self.pane_effects[(mac,i)]=self.effects[mac] if self.effects[mac] in self.pane_patterns.get((mac,i),{}) else "Solid"
                self.pane_overrides.add(mac)
                old=self.pane_states[(mac,index)]
                color=tuple(kwargs.get("rgbw_color",kwargs.get("rgb_color",old[2])))
                if len(color)==3:
                    color+=(0,)
                key=(mac,index)
                effect=kwargs.get("effect","Solid" if "rgbw_color" in kwargs or "rgb_color" in kwargs else self.pane_effects.get(key,"Solid"))
                if effect not in ("Solid",*self.pane_patterns.get(key,{})):
                    raise HomeAssistantError("Effect requires a captured bush map")
                self.pane_effects[key]=effect
                self.pane_states[key]=(on,kwargs.get("brightness",old[1]),color)
                if starting:
                    await self.mode("rt")
                if self.any_on():
                    await self.send()
            except BaseException:
                self.pane_states=old_states
                self.pane_overrides=old_overrides
                self.pane_effects=old_effects
                if starting:
                    await asyncio.gather(self.restore(),return_exceptions=True)
                raise
            if not self.any_on():
                await self.finish_idle()
                return
            if starting:
                self.active=True
                self.error=False
                self.task=self.hass.async_create_background_task(self.run(),"Twinkly virtual pane frames")
            self.notify()

    async def run(self):
        failures=0
        while self.active:
            moving=any(self.state[k][0] and effect in MOVING_SPIRALS for k,effect in self.effects.items())
            await asyncio.sleep((1/15 if self.master["role"]!="none" else (.2 if moving else .75)) if failures==0 else min(10,2**failures))
            async with self.lock:
                if not self.active:
                    return
                try:
                    if failures:
                        await self.validate()
                        current=await self.call(self.master,MODE,token=self.tokens[self.master["mac"]])
                        if current.get("mode")!="rt":
                            self.active=False
                            self.task=None
                            self.state={k:(False,v[1],v[2]) for k,v in self.state.items()}
                            self.pane_overrides.clear()
                            self.pane_states.clear()
                            self.error=False
                            self.notify()
                            return
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
            await self.shutdown("movie" if movie else "off")
            self.error=False
            self.state={k:(False,v[1],v[2]) for k,v in self.state.items()}
            self.pane_overrides.clear()
            self.pane_states.clear()
            self.notify()

async def update_together(targets,on,kwargs,combined=False):
    """Stage all zones under the common lock; start/send concurrently."""
    if not targets:
        return
    async with targets[0][0].lock:
        targets=[(g,members) for g,members in targets if on or g.active or g.error]
        if not targets:
            return
        changes=[(g,g.prepare(members,on,kwargs)) for g,members in targets]
        starting=[g for g,_ in changes if on and not g.active]
        # Validate all new sessions before touching playback mode.
        results=await asyncio.gather(*(g.prepare_start() for g in starting),return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException):
                raise result
        old=[(g,{k:(g.state[k],g.effects[k],k in g.combined_selected) for k in values}) for g,values in changes]
        old_panes=[(g,g.pane_overrides.copy()) for g,_ in changes]
        try:
            results=await asyncio.gather(*(g.mode("rt") for g in starting),return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException):
                    raise result
            for g,values in changes:
                for key,(state,effect) in values.items():
                    g.state[key]=state
                    g.effects[key]=effect
                    g.pane_overrides.discard(key)
                    if combined and key in g.combined_geometry:
                        g.combined_selected.add(key)
                    else:
                        g.combined_selected.discard(key)
            results=await asyncio.gather(*(g.send() for g,_ in changes if g.any_on()),return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException):
                    raise result
        except BaseException:
            for g,overrides in old_panes:
                g.pane_overrides=overrides
            for g,values in old:
                for key,(state,effect,was_combined) in values.items():
                    g.state[key]=state
                    g.effects[key]=effect
                    if was_combined:
                        g.combined_selected.add(key)
                    else:
                        g.combined_selected.discard(key)
            await asyncio.gather(*(g.restore() for g in starting),return_exceptions=True)
            # Active streams resend their previous state on the next tick.
            raise
        # Off operations are committed before hardware shutdown; never roll back
        # to a streaming state if an off command fails.
        idle=[g for g,_ in changes if not g.any_on()]
        results=await asyncio.gather(*(g.finish_idle() for g in idle),return_exceptions=True)
        for g in starting:
            g.active=True
            g.error=False
            g.task=g.hass.async_create_background_task(g.run(),"Twinkly virtual light frames")
        for g,_ in changes:
            g.notify()
        for result in results:
            if isinstance(result,BaseException):
                raise result


class CombinedBush(LightEntity):
    """One logical bush containing two independently addressable controllers."""
    _attr_should_poll=False
    _attr_supported_features=LightEntityFeature.EFFECT
    _attr_has_entity_name=True

    def __init__(self,name,slug,targets,ordered_parts):
        self.targets=targets
        self.parts=ordered_parts
        self.combined_map=None
        self._attr_unique_id="combined_"+slug
        self._attr_name=None
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,self._attr_unique_id)},name=name,manufacturer="Twinkly",model="Combined bush")
        self._attr_color_mode=ColorMode.RGBW if all(m["width"]==4 for _,m in self.parts) else ColorMode.RGB
        self._attr_supported_color_modes={self._attr_color_mode}

    def load_maps(self,data):
        # Definitions order is top then bottom; derived wire order is bottom first.
        (tg,top),(bg,bottom)=self.parts
        originals=data.get("_area_manager",{}).get("maps_3d",{})
        self.combined_map=None
        for g,m in self.parts:
            g.combined_geometry.pop(m["mac"],None)
        try:
            br,tr=originals[bottom["mac"]],originals[top["mac"]]
            if br["led_count"]!=bottom["led_count"] or tr["led_count"]!=top["led_count"]:
                raise ValueError("Archive LED count differs")
            model=combined_bush_map(br["layout"],tr["layout"],bottom["led_count"],top["led_count"])
            geo=spiral_geometry(model,bottom["led_count"]+top["led_count"])
            bg.combined_geometry[bottom["mac"]]=geo[:bottom["led_count"]]
            tg.combined_geometry[top["mac"]]=geo[bottom["led_count"]:]
            self.combined_map=model
        except (KeyError,ValueError,TypeError) as exc:
            _LOGGER.warning("%s: combined 3D map unavailable: %s",self._attr_unique_id,exc)

    @property
    def extra_state_attributes(self):
        return {"combined_3d_ready":self.combined_map is not None,
                "combined_led_count":sum(m["led_count"] for _,m in self.parts),
                "map_alignment":"estimated equal height, shared Y-up and azimuth"}

    @property
    def available(self):
        return all(not g.error for g,_ in self.parts)

    @property
    def is_on(self):
        return any(g.active and g.state[m["mac"]][0] for g,m in self.parts)

    def selected_states(self):
        states=[g.state[m["mac"]] for g,m in self.parts]
        return [s for s in states if s[0]] or states

    @property
    def brightness(self):
        states=self.selected_states()
        return round(sum(s[1] for s in states)/len(states))

    @property
    def rgbw_color(self):
        states=self.selected_states()
        return tuple(round(sum(s[2][i] for s in states)/len(states)) for i in range(4))

    @property
    def rgb_color(self):
        return self.rgbw_color[:3]

    @property
    def effect_list(self):
        common=set.intersection(*(set(g.patterns[m["mac"]]) for g,m in self.parts))
        if self.combined_map is None:
            common.difference_update(SPIRAL_EFFECTS)
        return ["Solid", *sorted(common)]

    @property
    def effect(self):
        effects={g.effects[m["mac"]] for g,m in self.parts}
        return next(iter(effects)) if len(effects)==1 else None

    async def async_turn_on(self,**kwargs):
        if "effect" in kwargs and kwargs["effect"] not in self.effect_list:
            raise HomeAssistantError("Effect is not available on both bush halves")
        await update_together(self.targets,True,kwargs,combined=True)

    async def async_turn_off(self,**kwargs):
        await update_together(self.targets,False,{},combined=True)


class VirtualLight(LightEntity):
    _attr_should_poll=False
    _attr_has_entity_name=True
    _attr_supported_features=LightEntityFeature.EFFECT
    def __init__(self,group,m):
        self.group=group
        self.member=m
        self._attr_name=None
        self._attr_unique_id=m["mac"]+"_virtual"
        self._attr_color_mode=ColorMode.RGBW if m["width"]==4 else ColorMode.RGB
        self._attr_supported_color_modes={self._attr_color_mode}
        mac=format_mac(m["mac"])
        connection=(CONNECTION_NETWORK_MAC,mac)
        # Own the device registration even when the native integration is absent.
        # MAC connection merges an existing device without changing entity unique IDs.
        self._attr_device_info=DeviceInfo(
            identifiers={(DOMAIN,m["mac"])},connections={connection},
            name=m["name"],manufacturer="Twinkly")
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
        mac=self.member["mac"]
        if mac in self.group.pane_overrides:
            return self.group.active and any(self.group.pane_states[(mac,i)][0] for i in range(len(self.group.pane_maps.get(mac,[]))))
        return self.group.active and self.group.state[mac][0]
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
        if self.group.active or self.group.error:
            await self.group.update(self.member,False,{})

class PaneLight(VirtualLight):
    _attr_supported_features=LightEntityFeature(0)

    def __init__(self,group,m,index):
        super().__init__(group,m)
        self.index=index
        self._attr_name=pane_label(m["name"],index)
        if " ".join(m["name"].casefold().split()) in ("front right","front right bush"):
            self._attr_supported_features=LightEntityFeature.EFFECT
        self._attr_unique_id=f"{m['mac']}_pane_{index+1}"

    @property
    def available(self):
        return super().available and self.member["mac"] in self.group.pane_maps

    def pane_state(self):
        mac=self.member["mac"]
        if mac in self.group.pane_overrides:
            return self.group.pane_states[(mac,self.index)]
        return self.group.state[mac]

    @property
    def is_on(self):
        return self.group.active and self.pane_state()[0]

    @property
    def brightness(self):
        return self.pane_state()[1]

    @property
    def rgbw_color(self):
        return self.pane_state()[2]

    @property
    def effect_list(self):
        if not self._attr_supported_features:
            return None
        return ["Solid",*self.group.pane_patterns.get((self.member["mac"],self.index),{})]

    @property
    def effect(self):
        if not self._attr_supported_features:
            return None
        key=(self.member["mac"],self.index)
        return self.group.pane_effects.get(key,"Solid") if key[0] in self.group.pane_overrides else self.group.effects[key[0]]

    @property
    def extra_state_attributes(self):
        maps=self.group.pane_maps.get(self.member["mac"])
        return {"pane_led_count":len(maps[self.index]) if maps else 0}

    async def async_turn_on(self,**kwargs):
        await self.group.update_pane(self.member,self.index,True,kwargs)

    async def async_turn_off(self,**kwargs):
        await self.group.update_pane(self.member,self.index,False,{})

async def async_setup_entry(hass,entry,async_add_entities):
    from . import query
    from .realtime import basic_group_members
    ctx=hass.data[DOMAIN][entry.entry_id]
    from .realtime import standalone_member
    selected=list(ctx["devices"])
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
                elif group.get("mode")=="none":
                    member=standalone_member(m,gestalt,group)
                    observations["standalone:"+member["mac"]]=[(m,gestalt,group)]
                else:
                    _LOGGER.warning("%s: unsupported group role %s",m["name"],group.get("mode"))
            except (ValueError,KeyError,TypeError, OSError) as exc:
                _LOGGER.warning("%s: virtual light discovery skipped: %s",m["name"],exc)
        data=await ctx["store"].async_load() or {}
        for uid,items in observations.items():
            try:
                members=[standalone_member(*items[0])] if uid.startswith("standalone:") else basic_group_members(items)
                group=Group(hass,members,ctx["lock"],query)
                group.load_maps(data)
                group.entities=[VirtualLight(group,m) for m in members]
                group.entities.extend(PaneLight(group,m,i) for m in members for i in range(DEFAULTS.get(" ".join(m["name"].casefold().split()),0)))
                groups.append(group)
            except (ValueError,KeyError,TypeError,StopIteration) as exc:
                _LOGGER.warning("Virtual group %s not created: %s; discovered %s",uid,exc,[(m["name"],g.get("mode"),g.get("offset"),g.get("size"),hw.get("number_of_led")) for m,hw,g in items])
    ctx["virtual_groups"]=groups
    combined=[]
    definitions=(
        ("Front Left Bush","front_left_bush",({"front left top"},{"front left bottom"})),
        ("Back Right Bush","back_right_bush",({"back bush top","backr bush top","back right bush top"},{"back bush bottom","backr bush bottom","back right bush bottom"})),
    )
    for name,slug,aliases in definitions:
        matches=[[(g,m) for g in groups for m in g.members if " ".join(m["name"].casefold().split()) in names] for names in aliases]
        if any(len(match)!=1 for match in matches):
            _LOGGER.warning("%s: expected exactly one controller per half; found %s",name,[len(x) for x in matches])
            continue
        targets=[]
        for match in matches:
            g,m=match[0]
            existing=next((members for owner,members in targets if owner is g),None)
            if existing is None:
                targets.append((g,[m]))
            else:
                existing.append(m)
        entity=CombinedBush(name,slug,targets,[match[0] for match in matches])
        entity.load_maps(data)
        for g,_ in targets:
            # Share notifications, but add the combined entity only once.
            g.entities.append(entity)
        combined.append(entity)
    ctx["combined_bushes"]=combined
    individual=[e for group in groups for e in group.entities if isinstance(e,VirtualLight)]
    async_add_entities(individual+combined)
