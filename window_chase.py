#!/usr/bin/env python3
"""Experimental additive Twinkly movie installer. No deletion or mode changes."""
import argparse
import colorsys
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import urllib.request
import uuid
from twinkly_inspector import get_devices, authenticate

ROOT=Path(__file__).resolve().parent
def load_module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/"custom_components/twinkly_area_state"/(name+".py"))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
panes=load_module("panes")
rt=load_module("realtime")
ORDER=("office left","office right","master bedroom","landing","lounge","bar")
NAME="Christmas Window Chase"

def norm(name):
    return " ".join(name.casefold().split())

def render(name,count,width,frames):
    if name not in ORDER or width not in (3,4) or not 16<=frames<=1200:
        raise ValueError("Invalid window, format or duration")
    zones=panes.pane_indices(name,count)
    start=sum(panes.DEFAULTS[n] for n in ORDER[:ORDER.index(name)])
    total=sum(panes.DEFAULTS[n] for n in ORDER)
    result=bytearray()
    for f in range(frames):
        phase=f/frames
        head=(total-1)*(1-math.cos(2*math.pi*phase))/2
        frame=bytearray(count*width)
        for p,indices in enumerate(zones):
            z=start+p
            distance=abs(z-head)
            level=.20+.80*math.exp(-distance**2/3.2)
            for i in indices:
                spark=max(0,math.cos(2*math.pi*(phase*11+i*.618+z*.13)))**22
                hue=(z/total+phase*2+i/count*.12)%1
                rgb=colorsys.hsv_to_rgb(hue,1-.55*spark,min(1,level+.35*spark))
                pixel=tuple(round(c*255) for c in rgb)
                frame[i*width:(i+1)*width]=bytes((0,)+pixel if width==4 else pixel)
        result.extend(frame)
    return bytes(result)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def call(ip,path,timeout,token=None,payload=None):
    headers={"Accept":"application/json"}
    if token:
        headers["X-Auth-Token"]=token
    if payload is not None:
        binary=isinstance(payload,bytes)
        headers["Content-Type"]="application/octet-stream" if binary else "application/json"
        body=payload if binary else json.dumps(payload).encode()
    else:
        body=None
    req=urllib.request.Request("http://"+ip+"/xled/v1/"+path,data=body,headers=headers)
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=timeout) as response:
        data=json.loads(response.read(1024*1024+1))
    if not isinstance(data,dict) or data.get("code")!=1000:
        raise ValueError(path+": controller rejected request")
    return data

def inspect(device,timeout):
    ip=device["ip"]
    g=call(ip,"gestalt",timeout)
    token,_=authenticate(ip,timeout)
    if not token:
        raise ValueError("Authentication failed")
    group=call(ip,"application/group",timeout,token)["group"]
    catalogue=call(ip,"movies",timeout,token)
    mode=call(ip,"led/mode",timeout,token)["mode"]
    return dict(name=norm(device["name"]),ip=ip,mac=g["mac"],count=g["number_of_led"],width=rt.pixel_width(g),group=group,catalogue=catalogue,mode=mode)

def capacity(record,frames):
    c=record["catalogue"]
    movies=c["movies"]
    if not isinstance(movies,list) or type(c["available_frames"]) is not int or c["available_frames"]<0:
        raise ValueError("Unknown movie capacity")
    if len(movies)>=16 or c["available_frames"]<frames:
        raise ValueError("Insufficient movie slots or frames; remove unwanted movie in official app")
    if any(m.get("name")==NAME for m in movies):
        raise ValueError("Movie name already exists; refusing duplicate")

def plan(records,frames,fps):
    if len({r["mac"] for r in records})!=len(records):
        raise ValueError("Duplicate controller identity")
    result=[]
    uid=str(uuid.uuid4())
    for r in records:
        capacity(r,frames)
        data=render(r["name"],r["count"],r["width"],frames)
        meta=dict(name=NAME,unique_id=uid,descriptor_type="rgbw_raw" if r["width"]==4 else "rgb_raw",leds_per_frame=r["count"],frames_number=frames,fps=fps)
        result.append((r,meta,data))
    return result

def upload(device,record,meta,data,timeout):
    if inspect(device,timeout)!=record:
        raise ValueError("Controller state changed; rerun preflight")
    token,_=authenticate(record["ip"],timeout)
    if not token:
        raise ValueError("Authentication failed")
    call(record["ip"],"movies/new",timeout,token,meta)
    call(record["ip"],"movies/full",timeout,token,data)
    after=inspect(device,timeout)
    old=record["catalogue"]["movies"]
    new=after["catalogue"]["movies"]
    if not all(m in new for m in old) or not any(all(m.get(k)==v for k,v in meta.items()) for m in new):
        raise ValueError("Catalogue readback differs; stop and inspect before retry")
    if after["group"]!=record["group"]:
        raise ValueError("Group changed unexpectedly")
    print(record["name"]+": uploaded and catalogue verified")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",default="devices.json")
    p.add_argument("--names",nargs="+",default=list(ORDER))
    p.add_argument("--fps",type=int,default=20,choices=(10,15,20,25))
    p.add_argument("--seconds",type=int,default=8,choices=range(4,31))
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--output",default=None,help="New private directory for movie binaries and metadata")
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    if not 0<a.timeout<=60:
        p.error("Invalid timeout")
    names=[norm(n) for n in a.names]
    devices=[d for d in get_devices(a) if norm(d["name"]) in names]
    if len(devices)!=len(names) or set(names)!={norm(d["name"]) for d in devices}:
        p.error("Selected names must match devices.json uniquely")
    records=[inspect(d,a.timeout) for d in devices]
    frames=a.fps*a.seconds
    for r in records:
        c=r["catalogue"]
        print(r["name"],"group:",r["group"].get("mode"),"free frames:",c.get("available_frames"),"capacity:",c.get("max_capacity"),"movies:",len(c["movies"]),"required:",frames)
    jobs=plan(records,frames,a.fps)
    if a.output:
        dest=Path(a.output)
        dest.mkdir(parents=True,exist_ok=False)
        manifest=[]
        for r,meta,data in jobs:
            filename=r["name"].replace(" ","-")+".bin"
            (dest/filename).write_bytes(data)
            manifest.append(dict(controller=r,metadata=meta,file=filename,sha256=hashlib.sha256(data).hexdigest()))
        (dest/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    if not a.execute:
        print("Read-only preflight; no movie writes.")
        return
    if not a.output:
        p.error("--execute requires --output for private backup and generated files")
    # First live trial is deliberately restricted to standalone hardware.
    if len(jobs)!=1 or any(r["group"].get("mode")!="none" or r["mode"] not in ("off","color") for r,_,_ in jobs):
        p.error("Experimental upload requires exactly one standalone controller in off or color mode; grouped sync is not yet verified")
    if input("Type UPLOAD to append movie without deleting anything: ")!="UPLOAD":
        return
    upload(devices[0],*jobs[0],a.timeout)

if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        print("STOP:",exc,"; inspect before retrying; partial upload possible.",file=sys.stderr)
        sys.exit(2)
