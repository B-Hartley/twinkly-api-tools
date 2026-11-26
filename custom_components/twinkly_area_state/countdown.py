"""Pure seven-segment digit masks for the joined Landing window."""
import math

# Screen coordinates: x increases right, y increases downward.
SEGMENTS = {
    "a": (.20,.13,.80,.13), "b": (.82,.19,.82,.44),
    "c": (.82,.56,.82,.82), "d": (.20,.87,.80,.87),
    "e": (.18,.56,.18,.82), "f": (.18,.19,.18,.44),
    "g": (.20,.50,.80,.50),
}
DIGITS = {5:"afgcd",4:"fgbc",3:"abgcd",2:"abged",1:"bc"}

def _distance(x,y,line):
    x1,y1,x2,y2=line
    dx,dy=x2-x1,y2-y1
    t=max(0,min(1,((x-x1)*dx+(y-y1)*dy)/(dx*dx+dy*dy)))
    return math.hypot(x-x1-t*dx,y-y1-t*dy)

def countdown_patterns(layout,count,width):
    """Build five fixed gold RGB(W) frames; preserve exact wire order."""
    if layout.get("source")!="2d" or count!=210 or width not in (3,4):
        raise ValueError("Countdown needs the captured 210-LED Landing 2D map")
    points=layout.get("coordinates",[])
    if len(points)!=count:
        raise ValueError("Incorrect Landing map length")
    xy=[(float(p["x"]),float(p["y"])) for p in points]
    if not all(math.isfinite(v) for p in xy for v in p):
        raise ValueError("Invalid Landing coordinates")
    left,right=min(p[0] for p in xy),max(p[0] for p in xy)
    bottom,top=min(p[1] for p in xy),max(p[1] for p in xy)
    if right-left<=1e-9 or top-bottom<=1e-9:
        raise ValueError("Degenerate Landing map")
    xy=[((x-left)/(right-left),(top-y)/(top-bottom)) for x,y in xy]
    gold=bytes((0,255,190,65) if width==4 else (255,190,65))
    black=bytes(width)
    frames={}
    for digit,segments in DIGITS.items():
        mask=[any(_distance(x,y,SEGMENTS[seg])<=.105 for seg in segments) for x,y in xy]
        if not any(mask) or all(mask):
            raise ValueError("Degenerate countdown digit mask")
        frames[f"Countdown {digit}"]=b"".join(gold if on else black for on in mask)
    if len(set(frames.values()))!=5:
        raise ValueError("Countdown digit masks are not distinct")
    return frames
