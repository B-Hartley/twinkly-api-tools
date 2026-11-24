"""Pure geometry and RGB(W) frame helpers for virtual panes."""
import math

STRANDS = {
    "office left": (2, 3, 3, 2), "office right": (2, 3, 3, 2),
    "landing": (4, 6), "master bedroom left": (5, 5),
    "master bedroom right": (5, 5), "bar": (2, 3, 3, 2),
    "lounge": (2, 3, 3, 2), "front right bush": (1, 1),
    "front right": (1, 1),
}
DEFAULTS = {name: len(parts) for name, parts in STRANDS.items()}

def strand_indices(count, parts, reverse=False):
    if type(count) is not int or count <= 0 or not parts or any(type(p) is not int or p <= 0 for p in parts) or count % sum(parts):
        raise ValueError("LED count does not divide into complete equal strands")
    width = count // sum(parts)
    order = list(reversed(parts)) if reverse else parts
    result = []
    start = 0
    for part in order:
        end = start + width * part
        result.append(list(range(start, end)))
        start = end
    return list(reversed(result)) if reverse else result


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
