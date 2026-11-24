"""Pure geometry and RGB(W) frame helpers for virtual panes."""
import math

DEFAULTS = {
    "office left": 3, "office right": 3, "landing": 2,
    "master bedroom left": 2, "master bedroom right": 2,
    "bar": 4, "lounge": 4, "front right bush": 2,
}

def split_indices(layout, count, panes, axis="x", boundaries=None, reverse=False):
    points = layout["coordinates"]
    if len(points) != count or axis not in ("x", "y", "z") or panes < 2:
        raise ValueError("Invalid pane geometry")
    values = [float(p[axis]) for p in points]
    if not values or not all(map(math.isfinite, values)):
        raise ValueError("Invalid coordinates")
    low, high = min(values), max(values)
    if low == high:
        raise ValueError("Degenerate pane axis")
    cuts = list(boundaries) if boundaries is not None else [i / panes for i in range(1, panes)]
    if len(cuts) != panes - 1 or any(not 0 < x < 1 for x in cuts) or cuts != sorted(set(cuts)):
        raise ValueError("Invalid pane boundaries")
    result = [[] for _ in range(panes)]
    for index, value in enumerate(values):
        position = (value - low) / (high - low)
        zone = sum(position >= cut for cut in cuts)
        result[panes - 1 - zone if reverse else zone].append(index)
    if not all(result):
        raise ValueError("A pane has no LEDs")
    return result

def composite(frame, width, indices, replacement):
    if len(replacement) != len(frame):
        raise ValueError("Frame size mismatch")
    for i in indices:
        frame[i*width:(i+1)*width] = replacement[i*width:(i+1)*width]
