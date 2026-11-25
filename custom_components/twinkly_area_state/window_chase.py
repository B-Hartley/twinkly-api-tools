"""Pure frame generation for the live Front window chase."""
WINDOW_CHASE = "Window Chase"
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

def chase_frame(member, zones, elapsed, brightness, rgbw):
    """One pane at a time, reversing at each end; other pixels remain black."""
    frame = bytearray(member["led_count"] * member["width"])
    if not zones or brightness <= 0:
        return bytes(frame)
    period = max(1, 2 * len(zones) - 2)
    step = int(max(0, elapsed) / PANE_SECONDS) % period
    index = min(step, period - step)
    mac, indices = zones[index]
    if mac != member["mac"]:
        return bytes(frame)
    r, g, b, w = (round(c * brightness / 255) for c in rgbw)
    pixel = bytes((w, r, g, b) if member["width"] == 4 else (r, g, b))
    for i in indices:
        start = i * member["width"]
        frame[start:start + member["width"]] = pixel
    return bytes(frame)
