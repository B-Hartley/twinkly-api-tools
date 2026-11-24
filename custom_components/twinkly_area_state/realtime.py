"""Experimental Twinkly v3 real-time RGB/RGBW frame encoder and startup animation."""
import base64
import colorsys

def packets(token, frame):
    key = base64.b64decode(token, validate=True)
    if len(key) != 8 or not frame or len(frame) > 900*256:
        raise ValueError("Invalid token or frame length")
    return [b"\x03"+key+b"\x00\x00"+bytes([i//900])+frame[i:i+900]
            for i in range(0,len(frame),900)]

def frame_at(members, t, effect="rainbow", channels=3):
    if channels not in (3,4) or effect not in ("rainbow","solid"):
        raise ValueError("Unsupported format/effect")
    result=bytearray()
    for index, member in enumerate(members):
        if member["offset"] != len(result)//channels or member["leds"] <= 0:
            raise ValueError("Noncontiguous LED ranges")
        level=max(0,min(1,(t-1-index*2)/2))
        for j in range(member["leds"]):
            rgb=(255,208,128) if effect=="solid" else tuple(
                int(v*255) for v in colorsys.hsv_to_rgb((j/member["leds"]+t*.15)%1,1,1))
            if channels==4:
                result.append(0) # RGBW devices use WRGB wire order
            result.extend(int(v*level) for v in rgb)
    return bytes(result)
