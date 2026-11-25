"""Pure frame generation for the live Front window chase."""
WINDOW_CHASE = "Window Chase"
HOUSE_CHASE = "House and Garden Chase"
CHASE_EFFECTS = (WINDOW_CHASE, HOUSE_CHASE)
WINDOW_ORDER = ("office left", "office right", "master bedroom", "landing", "lounge", "bar")
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
        zones.extend((m["mac"], tuple(pane)) for pane in panes)
    return zones

def house_zones(members, pane_maps):
    """One logical step per pane or bush; combined bushes share a step."""
    windows = chase_zones(members, pane_maps)
    if not windows:
        return []
    result = [(zone,) for zone in windows]
    def find(aliases):
        matches = [m for m in members if " ".join(m["name"].casefold().split()) in aliases]
        return matches[0] if len(matches) == 1 else None
    def whole(m):
        return (m["mac"], tuple(range(m["led_count"])))
    # Provisional route; keep top/bottom together and split Front Right.
    route = (
        ({"front left top"}, {"front left bottom"}),
        ({"back left bush", "back left"},),
        ({"light tree", "tree"},),
        ({"front right bush", "front right"},),
        ({"mid right", "mid right bush"},),
        ({"back bush top", "backr bush top", "back right bush top", "back right top"},
         {"back bush bottom", "backr bush bottom", "back right bush bottom", "back right bottom"}),
        ({"icicles"},),
    )
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
