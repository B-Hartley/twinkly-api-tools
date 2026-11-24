"""Optional Twinkly cloud session; independent of local light authentication."""
import asyncio
import time
import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

BASE="https://api.twinkly.com"
class CloudSession:
    def __init__(self,hass,entry_id):
        self.session=async_get_clientsession(hass)
        self.store=Store(hass,1,f"twinkly_area_state.cloud.{entry_id}",private=True)
        self.lock=asyncio.Lock()

    async def _post(self,path,fields):
        try:
            async with self.session.post(BASE+path,json=fields,allow_redirects=False,timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status!=200:
                    raise HomeAssistantError(f"Twinkly cloud authentication HTTP {response.status}")
                data=await response.json()
        except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):
            raise HomeAssistantError("Twinkly cloud authentication response unavailable") from None
        if not isinstance(data,dict) or str(data.get("token_type","")).casefold()!="bearer":
            raise HomeAssistantError("Invalid Twinkly cloud token reply")
        access=data.get("access_token")
        refresh=data.get("refresh_token")
        lifetime=data.get("expires_in")
        if any(not isinstance(v,str) or not v or len(v)>4096 or any(c.isspace() for c in v) for v in (access,refresh)):
            raise HomeAssistantError("Invalid Twinkly cloud tokens")
        if type(lifetime) not in (int,float) or not 0<lifetime<=2592000:
            raise HomeAssistantError("Invalid Twinkly cloud token expiry")
        result={"access_token":access,"refresh_token":refresh,"expires_at":time.time()+lifetime}
        # Save immediately: the previous refresh token may already be invalidated.
        await self.store.async_save(result)
        return result

    async def login(self,username,password):
        async with self.lock:
            await self._post("/v2/auth",{"username":username,"password":password})

    async def _token(self,force=False):
        data=await self.store.async_load()
        if not data:
            raise HomeAssistantError("Configure Twinkly cloud login in integration options first")
        if not force and data["expires_at"]>time.time()+300:
            return data["access_token"]
        return (await self._post("/v2/auth/refresh",{"refresh_token":data["refresh_token"]}))["access_token"]

    async def get(self,path):
        if path not in ("/v3/installations","/v3/objects?mine=true&fields=layout,devices,capabilities"):
            raise ValueError("Unsupported cloud read")
        async with self.lock:
            token=await self._token()
            for attempt in range(2):
                try:
                    async with self.session.get(BASE+path,headers={"Authorization":"Bearer "+token},allow_redirects=False,timeout=aiohttp.ClientTimeout(total=20)) as response:
                        if response.status==401 and attempt==0:
                            pass
                        elif response.status==200:
                            return await response.json()
                        else:
                            raise HomeAssistantError(f"Twinkly cloud read HTTP {response.status}")
                except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):
                    raise HomeAssistantError("Twinkly cloud read failed") from None
                token=await self._token(force=True)
        raise HomeAssistantError("Twinkly cloud authorization failed")

    async def forget(self):
        async with self.lock:
            await self.store.async_remove()
