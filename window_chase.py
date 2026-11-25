#!/usr/bin/env python3
"""Experimental additive Twinkly movie installer. No deletion or mode changes."""
import argparse
import hashlib
import importlib.util
import json
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
NAME="Window Chase (stored)"
chase=load_module("window_chase")

def norm(name):
    return " ".join(name.casefold().split())

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


def group_records(records):
    masters=[r for r in records if r["group"].get("mode")=="master"]
    if len(masters)!=1:
        raise ValueError("Select a complete joined group with exactly one master")
    master=masters[0]
    uid=master["group"].get("uid")
    total=sum(r["count"] for r in records)
    if not uid or any(r["group"].get("uid")!=uid or r["group"].get("size")!=total or r["group"].get("mode") not in ("master","slave") for r in records):
        raise ValueError("Incomplete or inconsistent physical group")
    spans=[]
    for r in records:
        g=r["group"]
        offset=g.get("offset")
        if offset is None and g["mode"]=="master":
            offset=0
        if type(offset) is not int or offset<0:
            raise ValueError("Invalid group offset")
        spans.append((offset,offset+r["count"]))
    end=0
    for low,high in sorted(spans):
        if low!=end or high<=low:
            raise ValueError("Group offsets overlap or have gaps")
        end=high
    if end!=total or len({r["mac"] for r in records})!=len(records):
        raise ValueError("Invalid group coverage or duplicate MAC")
    return master

def build(records,fps=10,step_frames=3,uid=None):
    group_records(records)
    members=[dict(name=r["name"],mac=r["mac"],led_count=r["count"],width=r["width"]) for r in records]
    maps={m["mac"]:panes.pane_indices(m["name"],m["led_count"]) for m in members if m["name"] in ORDER}
    zones=chase.chase_zones(members,maps)
    if not zones:
        raise ValueError("All six mapped windows required")
    route=zones+zones[-2:0:-1]
    frames=len(route)*step_frames
    uid=uid or str(uuid.uuid4())
    jobs=[]
    for r,m in zip(records,members):
        capacity(r,frames)
        data=bytearray()
        for mac,indices in route:
            frame=bytearray(r["count"]*r["width"])
            if mac==r["mac"]:
                pixel=bytes((0,255,180,80) if r["width"]==4 else (255,180,80))
                for i in indices:
                    frame[i*r["width"]:(i+1)*r["width"]]=pixel
            data.extend(frame*step_frames)
        meta=dict(name=NAME,unique_id=uid,descriptor_type="rgbw_raw" if r["width"]==4 else "rgb_raw",leds_per_frame=r["count"],frames_number=frames,fps=fps)
        jobs.append((r,meta,bytes(data)))
    return jobs

def append(record,meta,data,timeout):
    device={"name":record["name"],"ip":record["ip"]}
    before=inspect(device,timeout)
    if before!=record:
        raise ValueError("Controller changed since preflight: "+record["name"])
    token,_=authenticate(record["ip"],timeout)
    if not token:
        raise ValueError("Authentication failed")
    call(record["ip"],"movies/new",timeout,token,meta)
    call(record["ip"],"movies/full",timeout,token,data)
    after=inspect(device,timeout)
    movies=after["catalogue"]["movies"]
    if not all(m in movies for m in record["catalogue"]["movies"]) or not any(all(m.get(k)==v for k,v in meta.items()) for m in movies):
        raise ValueError("Movie catalogue verification failed")
    if after["group"]!=record["group"] or after["mode"]!=record["mode"]:
        raise ValueError("Group or mode changed unexpectedly")
    print(record["name"]+": catalogue verified",flush=True)

def main():
    p=argparse.ArgumentParser(description="Render a native group movie; writes are experimental and opt-in.")
    p.add_argument("--config",default="devices.json")
    p.add_argument("--output",required=True)
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    devices=get_devices(a)
    records=[inspect(d,a.timeout) for d in devices]
    window_records=[r for r in records if r["name"] in ORDER]
    if len(window_records)!=6:
        p.error("Exactly six named windows must be configured")
    uid=window_records[0]["group"].get("uid")
    if not uid:
        p.error("Windows must belong to the joined Front group")
    selected=[r for r in records if r["group"].get("uid")==uid]
    master=group_records(selected)
    jobs=build(selected)
    dest=Path(a.output)
    dest.mkdir(parents=True,exist_ok=False)
    manifest=[]
    for r,meta,data in jobs:
        filename=r["mac"].replace(":","").replace("-","")+".bin"
        (dest/filename).write_bytes(data)
        manifest.append(dict(controller=r,metadata=meta,file=filename,sha256=hashlib.sha256(data).hexdigest()))
        print(r["name"],"frames:",meta["frames_number"],"free:",r["catalogue"]["available_frames"],"mode:",r["mode"])
    (dest/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    if not a.execute:
        print("Read-only preflight; no movie writes.")
        return
    if any(r["mode"]!="off" for r in selected):
        p.error("Stop HA streaming and switch all group controllers off before uploading")
    if input("Type UPLOAD FRONT to attempt additive group movie upload: ")!="UPLOAD FRONT":
        return
    # Recheck every controller before the first write; master is appended last.
    for r in selected:
        if inspect({"name":r["name"],"ip":r["ip"]},a.timeout)!=r:
            raise ValueError("Preflight changed; no upload attempted")
    for r,meta,data in sorted(jobs,key=lambda job:job[0] is master):
        append(r,meta,data,a.timeout)
    print("All catalogues verified. Reload Area Manager to expose the shared movie. Playback sync and cloud visibility remain unverified.")

if __name__=="__main__":
    try:
        main()
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        print("STOP:",exc,"; inspect before retrying; partial upload possible.",file=sys.stderr)
        sys.exit(2)
