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

def diagnostic_frame(members, t, channels=3, axis="y", invert=False):
    """Ten seconds per solid isolation test, then opposing moving rainbows."""
    import math
    if channels not in (3,4) or axis not in ("x","y","z"):
        raise ValueError("Unsupported format or axis")
    result=bytearray()
    for m in members:
        name=m["name"].casefold()
        if name not in ("office left","office right") or m["offset"] != len(result)//channels:
            raise ValueError("Unexpected Office zone or offset")
        coords=m["area_layout"]["coordinates"]
        values=[float(c[axis]) for c in coords]
        if len(values)!=m["leds"] or not all(map(math.isfinite,values)) or max(values)<=min(values):
            raise ValueError("Invalid vertical mapping axis for "+m["name"])
        lo,hi=min(values),max(values)
        left=name=="office left"
        for v in values:
            if t<10:
                rgb=(255,0,0) if left else (0,0,0)
            elif t<20:
                rgb=(0,0,0) if left else (0,255,0)
            else:
                height=(v-lo)/(hi-lo)
                if invert:
                    height=1-height
                phase=height if left else -height
                rgb=tuple(int(c*255) for c in colorsys.hsv_to_rgb((phase+(t-20)*.25)%1,1,1))
            if channels==4:
                result.append(0)
            result.extend(rgb)
    return bytes(result)

def pixel_width(gestalt):
    """Detect wire pixel width; never silently guess conflicting metadata."""
    width=gestalt.get("bytes_per_led")
    profile=gestalt.get("led_profile")
    expected={"RGB":3,"RGBW":4,"WRGB":4}.get(str(profile).upper())
    if width is None:
        width=expected
    if type(width) is not int or width not in (3,4):
        raise ValueError("Unknown LED pixel width")
    if expected is not None and expected!=width:
        raise ValueError("Conflicting LED profile and pixel width")
    return width
