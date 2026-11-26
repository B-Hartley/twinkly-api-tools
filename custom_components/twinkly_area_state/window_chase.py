"""Pure frame generation for the live Front window chase."""
import colorsys
import math

WINDOW_CHASE = "Window Chase"
HOUSE_CHASE = "House and Garden Chase"
SLIDE_CHASE = "Bush and Window Chase V2"
CHASE_EFFECTS = (WINDOW_CHASE, HOUSE_CHASE, SLIDE_CHASE)
WINDOW_ORDER = ("lounge", "bar", "office right", "office left", "landing", "master bedroom")
# Physical pane labels run left to right; the return across the upper floor runs right to left.
REVERSE_ROUTE_WINDOWS = frozenset(("office right", "office left", "landing", "master bedroom"))
PANE_SECONDS = 0.32

def chase_zones(members, pane_maps):
    """Require all six named windows and complete, nonoverlapping pane maps."""
    zones = []
    for name in WINDOW_ORDER:
        matches = [m for m in members if " ".join(m["name"].casefold().split()) == name]
        if len(matches) != 1:
            return []
        m = matches[0]
        panes = pane_maps.get(m["mac"], [])
        if not panes or any(not pane for pane in panes):
            return []
        indices = [i for pane in panes for i in pane]
        if sorted(indices) != list(range(m["led_count"])):
            return []
        zones.extend((m["mac"], tuple(pane)) for pane in (reversed(panes) if name in REVERSE_ROUTE_WINDOWS else panes))
    return zones

def house_zones(members, pane_maps):
    """One logical step per pane or bush; combined bushes share a step."""
    windows = chase_zones(members, pane_maps)
    if not windows:
        return []
    result = []
    def find(aliases):
        matches = [m for m in members if " ".join(m["name"].casefold().split()) in aliases]
        return matches[0] if len(matches) == 1 else None
    def whole(m):
        return (m["mac"], tuple(range(m["led_count"])))
    route = SLIDE_BUSHES
    for aliases in route:
        parts = [find(a) for a in aliases]
        if any(m is None for m in parts):
            return []
        if aliases[0] == {"front right bush", "front right"}:
            m = parts[0]
            panes = pane_maps.get(m["mac"], [])
            if len(panes) != 2 or any(not p for p in panes) or sorted(i for p in panes for i in p) != list(range(m["led_count"])):
                return []
            result.extend(((m["mac"], tuple(p)),) for p in panes)
        else:
            result.append(tuple(whole(m) for m in parts))
    # The garden finishes beside the lounge; the upper floor is the return leg.
    result.extend((zone,) for zone in windows)
    icicles=find({"icicles"})
    if icicles is None:
        return []
    result.insert(len(result)-10, (whole(icicles),))
    return result

def chase_frame(member, zones, elapsed, brightness, rgbw, loop=False):
    """One pane at a time, reversing at each end; other pixels remain black."""
    frame = bytearray(member["led_count"] * member["width"])
    if not zones or brightness <= 0:
        return bytes(frame)
    period = len(zones) if loop else max(1, 2 * len(zones) - 2)
    step = int(max(0, elapsed) / PANE_SECONDS) % period
    index = step if loop else min(step, period - step)
    selected = zones[index] if loop else (zones[index],)
    indices = [i for mac, pixels in selected if mac == member["mac"] for i in pixels]
    if not indices:
        return bytes(frame)
    r, g, b, w = (round(c * brightness / 255) for c in rgbw)
    pixel = bytes((w, r, g, b) if member["width"] == 4 else (r, g, b))
    for i in indices:
        start = i * member["width"]
        frame[start:start + member["width"]] = pixel
    return bytes(frame)


# Each stop has a fully illuminated hold followed by a feathered spatial transfer.
BUSH_HOLD, BUSH_TRANSFER = .35, 1.55
PANE_HOLD, PANE_TRANSFER = .25, .95
SLIDE_WINDOWS = WINDOW_ORDER
SLIDE_BUSHES = (
    (("front left top",), ("front left bottom",)),
    (("light tree", "tree"),),
    (("front right bush", "front right"),),
    (("mid right", "mid right bush"),),
    (("back bush top", "backr bush top", "back right bush top", "back right top"),
     ("back bush bottom", "backr bush bottom", "back right bush bottom", "back right bottom")),
    (("back left bush", "back left", "backl bush"),),
)

def _points(layout, count, source, axes):
    if layout.get("source") != source:
        raise ValueError("Expected "+source+" geometry")
    pts=tuple(tuple(float(p[a]) for a in axes) for p in layout.get("coordinates", []))
    if len(pts)!=count or not all(math.isfinite(v) for p in pts for v in p):
        raise ValueError("Invalid LED coordinates")
    return pts

def _center(pts):
    return tuple(sum(v)/len(pts) for v in zip(*pts))

def _dot(a,b):
    return sum(x*y for x,y in zip(a,b))

def _unit(v):
    length=math.sqrt(_dot(v,v))
    return tuple(x/length for x in v) if length>1e-9 else None

def _project3(points, screen):
    """Fit the 3D sweep direction to its captured 2D projected direction.

    Coordinates are independent local captures, not a surveyed 3D world map.
    Standardise XYZ, use their correlation with the screen sweep, then align
    the projected extent to the screen extent (also joins stacked halves).
    """
    center=_center(points)
    scale=tuple(math.sqrt(sum((p[j]-center[j])**2 for p in points)/len(points)) for j in range(3))
    normalized=[tuple((p[j]-center[j])/scale[j] if scale[j]>1e-9 else 0 for j in range(3)) for p in points]
    average=sum(screen)/len(screen)
    weights=tuple(sum(p[j]*(v-average) for p,v in zip(normalized,screen)) for j in range(3))
    if _unit(weights) is None:
        raise ValueError("3D map has no usable alignment to group direction")
    raw=[_dot(p,weights) for p in normalized]
    lo,hi=min(raw),max(raw)
    if hi-lo<=1e-9:
        raise ValueError("Degenerate 3D sweep")
    a,b=min(screen),max(screen)
    return [a+(b-a)*(v-lo)/(hi-lo) for v in raw]

def slide_geometry(members,pane_maps,maps_3d,maps_2d):
    """Complete route with a continuous, normalized LED coordinate per zone."""
    by_mac={m["mac"]:m for m in members}
    def find(aliases):
        found=[m for m in members if " ".join(m["name"].casefold().split()) in aliases]
        if len(found)!=1:
            raise ValueError("Missing or ambiguous route zone: "+aliases[0])
        return found[0]
    def panes(m):
        result=pane_maps.get(m["mac"],[])
        if not result or any(not p for p in result) or sorted(i for p in result for i in p)!=list(range(m["led_count"])):
            raise ValueError("Incomplete pane map: "+m["name"])
        return [(m["mac"],tuple(p)) for p in result]
    def whole(m):
        return (m["mac"],tuple(range(m["led_count"])))
    route=[]
    for spec in SLIDE_BUSHES:
        ms=[find(aliases) for aliases in spec]
        if spec[0][0]=="front right bush":
            parts=panes(ms[0])
            if len(parts)!=2:
                raise ValueError("Front Right must have two bushes")
            route.extend((True,(part,)) for part in parts)
        else:
            route.append((True,tuple(whole(m) for m in ms)))
    for name in SLIDE_WINDOWS:
        parts=panes(find((name,)))
        if name in REVERSE_ROUTE_WINDOWS:
            parts.reverse()
        route.extend((False,(part,)) for part in parts)
        if name=="bar":
            route.append((False,(whole(find(("icicles",))),)))
    screen={}
    originals={}
    for bush,parts in route:
        for mac,_ in parts:
            m=by_mac[mac]
            if mac not in screen:
                screen[mac]=_points(maps_2d.get(mac,{}),m["led_count"],"2d",("x","y"))
            if bush and mac not in originals:
                originals[mac]=_points(maps_3d.get(mac,{}),m["led_count"],"3d",("x","y","z"))
    centers=[_center([screen[mac][i] for mac,indices in parts for i in indices]) for _,parts in route]
    stops=[]
    seen=set()
    for z,(bush,parts) in enumerate(route):
        # Blend incoming/outgoing directions; when they cancel, prefer exit.
        here=centers[z]
        prev=centers[z-1]
        nxt=centers[(z+1)%len(route)]
        enter=_unit(tuple(a-b for a,b in zip(here,prev)))
        leave=_unit(tuple(a-b for a,b in zip(nxt,here)))
        direction=_unit(tuple(a+b for a,b in zip(enter,leave))) if enter and leave else None
        direction=direction or leave or enter or (1,0)
        projections=[]
        for mac,indices in parts:
            values=[_dot(screen[mac][i],direction) for i in indices]
            if bush:
                values=_project3([originals[mac][i] for i in indices],values)
            projections.extend((mac,i,v) for i,v in zip(indices,values))
        lo=min(v for _,_,v in projections)
        hi=max(v for _,_,v in projections)
        if hi-lo<=1e-9:
            raise ValueError("Degenerate route zone projection")
        pixels={}
        for mac,i,v in projections:
            if (mac,i) in seen:
                raise ValueError("Duplicate route LED")
            seen.add((mac,i))
            pixels.setdefault(mac,[]).append((i,(v-lo)/(hi-lo)))
        stops.append(dict(bush=bush,pixels=pixels,span=hi-lo,
                          hold=BUSH_HOLD if bush else PANE_HOLD))
    # Scale the transfer time using both projected widths, within practical
    # limits. The footprint itself grows/shrinks to fill each zone exactly.
    for i,stop in enumerate(stops):
        following=stops[(i+1)%len(stops)]
        peers=[s["span"] for s in stops if s["bush"]==stop["bush"]]
        median=sorted(peers)[len(peers)//2]
        ratio=(stop["span"]+following["span"])/(2*median)
        nominal=BUSH_TRANSFER if stop["bush"] or following["bush"] else PANE_TRANSFER
        stop["transfer"]=nominal*max(.8,min(1.3,math.sqrt(ratio)))
    return stops

def slide_duration(stops):
    return sum(s["hold"]+s["transfer"] for s in stops)

def _smooth(x):
    x=max(0,min(1,x))
    return x*x*(3-2*x)

def slide_frame(member,stops,elapsed,brightness,rgbw):
    """Moving spatial wipe, full stop at each zone, with a short rainbow wake.

    The bright head changes colour gradually around the loop. A feathered
    boundary crosses actual mapped pixels instead of fading a whole zone.
    """
    width=member["width"]
    frame=bytearray(member["led_count"]*width)
    if not stops or brightness<=0:
        return bytes(frame)
    t=elapsed%slide_duration(stops)
    for index,stop in enumerate(stops):
        duration=stop["hold"]+stop["transfer"]
        if t<duration:
            break
        t-=duration
    else:
        index,stop,t=0,stops[0],0
    progress=max(0,min(1,(t-stop["hold"])/stop["transfer"]))
    feather=.17
    boundary=progress*(1+2*feather)-feather
    next_index=(index+1)%len(stops)
    mac=member["mac"]
    # Gold at the start, then pastel spectral colours; selected RGB controls
    # the head's base tint. The dim saturated wake never exceeds 16%.
    phase=(index+progress)/len(stops)
    hue=(phase+.10)%1
    head=colorsys.hsv_to_rgb(hue,.48,1)
    base=tuple(.58*c+.42*255*v for c,v in zip(rgbw[:3],head))
    def draw(zone,incoming):
        for i,pos in zone["pixels"].get(mac,()):
            swept=_smooth((boundary-pos+feather)/(2*feather))
            level=swept if incoming else 1-swept
            # Wake fades out completely at the ends of every transfer.
            wake=0 if incoming else .16*math.sin(math.pi*progress)*math.exp(-((boundary-pos-.24)/.26)**2)
            tail=colorsys.hsv_to_rgb((hue-pos*.65-.18)%1,1,1)
            rgb=tuple(round(min(255,base[j]*level+255*tail[j]*wake)*brightness/255) for j in range(3))
            pixel=(0,*rgb) if width==4 else rgb
            frame[i*width:(i+1)*width]=bytes(pixel)
    draw(stop,False)
    draw(stops[next_index],True)
    return bytes(frame)
