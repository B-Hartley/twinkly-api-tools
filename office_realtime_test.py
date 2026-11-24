#!/usr/bin/env python3
"""Bounded Office group streaming experiment. Run on the Twinkly LAN."""
import argparse
import importlib.util
from pathlib import Path
import socket
import time
from group_test import load_plan, GROUP_API, LAYOUT_API, layout_matches
from movie_retention import read_member, compare
from cloud_group_cycle_reverse import archived_group_reference, apply_area_reference
from cloud_group_live import read_local
from cloud_group_preflight import mac
from resume_regroup import read_state, login
from twinkly_inspector import request

spec=importlib.util.spec_from_file_location("rt",Path(__file__).parent/"custom_components/twinkly_area_state/realtime.py")
rt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
MODE="/xled/v1/led/mode"

def checked(ip,path,token,**kwargs):
    r=request(ip,path,token=token,**kwargs)
    if not r["ok"]:
        raise RuntimeError(f"{ip} {path}: {r.get('error',r.get('data'))}")
    return r["data"]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan",default="private-area-plan.json")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--finish",choices=["movie","restore"],default="movie")
    p.add_argument("--effect",choices=["solid","rainbow","diagnostic","colour-swap"],default="rainbow")
    p.add_argument("--channels",type=int,choices=[3,4],help="Override only if investigating device metadata")
    p.add_argument("--transport",choices=["master","direct"],default="direct")
    p.add_argument("--rt-mode",choices=["master","both"],default="master",help="Which controllers receive the RT mode command")
    p.add_argument("--wire",choices=["udp","http"],default="udp")
    p.add_argument("--vertical-axis",choices=["x","y","z"],default="y")
    p.add_argument("--invert-vertical",action="store_true")
    a=p.parse_args()
    if a.wire=="http" and a.transport!="direct":
        p.error("HTTP test requires direct transport")
    plan,uid=load_plan(a.plan,require_verified=a.execute)
    members=sorted(plan["members"],key=lambda m:m["offset"])
    if len(members)!=2 or {m["name"].lower() for m in members}!={"office left","office right"}:
        raise ValueError("Office pair only")
    # Use exactly the same authoritative geometry as cloud regroup.
    local=[read_local(m,5) for m in plan["members"]]
    identities=[mac(x["gestalt"]["mac"]) for x in local]
    formats=[]
    for m,x in zip(plan["members"],local):
        g=x["gestalt"]
        n=rt.pixel_width(g)
        formats.append(n)
        fw=checked(m["ip"],"/xled/v1/fw/version",None).get("version","")
        print(f"{m['name']}: profile={g.get('led_profile')}, bytes_per_led={g.get('bytes_per_led')}, family={g.get('fw_family')}, firmware={fw}, detected={n}")
        if g.get("fw_family")=="D" or tuple(int(v) for v in fw.split(".")[:3])<(2,4,14):
            raise ValueError("This experiment requires UDP protocol v3 firmware")
    if len(set(formats))!=1:
        raise ValueError("Mixed pixel formats not yet supported")
    if a.channels is not None and a.channels!=formats[0]:
        raise ValueError("Requested channels conflict with live device format; omit --channels")
    a.channels=formats[0]
    print(f"Using {a.channels} bytes per LED; transport={a.transport}")
    _, layouts=archived_group_reference(plan,identities,("cloud-preflight-backups","cloud-audits"))
    plan=apply_area_reference(plan,layouts)
    members=sorted(plan["members"],key=lambda m:m["offset"])
    # Cloud regroup may generate a fresh UID; validate the live group, not stale UID.
    master=members[0]
    uid=checked(master["ip"],GROUP_API,login(master["ip"],5))["group"]["uid"]
    import uuid
    uid=str(uuid.UUID(uid))
    for m in members:
        if read_state(m,uid,plan["size"],5)!={"group":"expected","layout":"area"}:
            raise RuntimeError("Regroup and verify first")
    for m in members:
        print(m["name"],m["offset"],m["leds"])
    if a.effect=="diagnostic":
        for t in (0,10,20):
            rt.diagnostic_frame(members,t,a.channels,a.vertical_axis,a.invert_vertical)
    if not a.execute:
        print("Read-only preflight passed")
        return
    before=[read_member(m,5) for m in members]
    if a.finish=="movie" and not before[0]["movies"]:
        raise RuntimeError("No native movies on master")
    targets=members[:1] if a.transport=="master" else members
    sessions=[]
    for m in targets:
        ip=m["ip"]
        token=login(ip,5)
        old=checked(ip,MODE,token)["mode"]
        print(f"{m['name']}: initial mode={old}")
        if (m is master or a.rt_mode=="both") and old not in ("off","movie","playlist","color","effect","rt"):
            raise RuntimeError("Unsupported original mode: "+old)
        sessions.append((m,token,old))
    import urllib.request
    import json
    def http_frame(ip, token, data):
        req=urllib.request.Request(f"http://{ip}/xled/v1/led/rt/frame",data=data,headers={"X-Auth-Token":token,"Content-Type":"application/octet-stream"},method="POST")
        with urllib.request.urlopen(req,timeout=3) as response:
            reply=json.loads(response.read())
        if reply.get("code")!=1000:
            raise RuntimeError(f"{ip}: HTTP frame rejected: {reply}")
    attempted=[]
    completed=False
    try:
        for m,token,old in (sessions if a.rt_mode=="both" else sessions[:1]):
            attempted.append((m,old))
            checked(m["ip"],MODE,token,method="POST",payload={"mode":"rt"})
            print(f"{m['name']}: RT mode requested")
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            start=time.monotonic()
            phase=None
            duration=40 if a.effect=="colour-swap" else 30 if a.effect=="diagnostic" else 10
            while (t:=time.monotonic()-start)<duration:
                if a.effect=="colour-swap":
                    current=min(3,int(t//10))
                    frame=rt.colour_swap_frame(members,current,a.channels)
                    if phase!=current:
                        print(["LEFT RED / RIGHT BLUE","LEFT BLUE / RIGHT RED","LEFT GREEN / RIGHT PURPLE","LEFT PURPLE / RIGHT GREEN"][current],flush=True)
                        phase=current
                elif a.effect=="diagnostic":
                    current=0 if t<10 else 1 if t<20 else 2
                    frame=rt.diagnostic_frame(members,t,a.channels,a.vertical_axis,a.invert_vertical)
                    if phase!=current:
                        print(["LEFT RED / RIGHT OFF","LEFT OFF / RIGHT GREEN","OPPOSING RAINBOWS"][current],flush=True)
                        for member in members:
                            start_byte=member["offset"]*a.channels
                            data=frame[start_byte:start_byte+member["leds"]*a.channels]
                            lit=sum(any(data[i:i+a.channels]) for i in range(0,len(data),a.channels))
                            print(f"  {member['name']}: {len(data)} bytes, {lit}/{member['leds']} nonblack pixels",flush=True)
                        phase=current
                else:
                    frame=rt.frame_at(members,t,a.effect,a.channels)
                for m,token,old in sessions:
                    data=frame if a.transport=="master" else frame[m["offset"]*a.channels:(m["offset"]+m["leds"])*a.channels]
                    if len(data)!=(plan["size"] if a.transport=="master" else m["leds"])*a.channels:
                        raise RuntimeError("Unexpected frame length")
                    if a.wire=="http":
                        http_frame(m["ip"],token,data)
                    else:
                        for packet in rt.packets(token,data):
                            sock.sendto(packet,(m["ip"],7777))
                time.sleep(.25 if a.wire=="http" else .04)
        completed=True
    finally:
        errors=[]
        for m,old in reversed(attempted):
            try:
                token=login(m["ip"],5)
                checked(m["ip"],MODE,token,method="POST",payload={"mode":"movie" if completed and a.finish=="movie" else old})
                print("Handover",m["name"],"movie" if completed and a.finish=="movie" else old)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("Mode restoration failed: "+"; ".join(errors))
    for m in members:
        if read_state(m,uid,plan["size"],5)!={"group":"expected","layout":"area"}:
            raise RuntimeError("Post-test group/layout mismatch")
    after=[read_member(m,5) for m in members]
    if not all(x["preserved"] for x in compare(before,after)):
        raise RuntimeError("Movie catalogue or playlist changed")
    print("Group/layout and movie catalogue verified; visually confirm animation and handover")

if __name__=="__main__":
    main()
