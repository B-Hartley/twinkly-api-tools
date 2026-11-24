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
    p.add_argument("--effect",choices=["solid","rainbow","diagnostic"],default="rainbow")
    p.add_argument("--channels",type=int,choices=[3,4],required=True)
    p.add_argument("--transport",choices=["master","direct"],default="master")
    p.add_argument("--vertical-axis",choices=["x","y","z"],default="y")
    p.add_argument("--invert-vertical",action="store_true")
    a=p.parse_args()
    plan,uid=load_plan(a.plan,require_verified=a.execute)
    members=sorted(plan["members"],key=lambda m:m["offset"])
    if len(members)!=2 or {m["name"].lower() for m in members}!={"office left","office right"}:
        raise ValueError("Office pair only")
    # Use exactly the same authoritative geometry as cloud regroup.
    identities=[mac(read_local(m,5)["gestalt"]["mac"]) for m in plan["members"]]
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
        if old not in ("off","movie","playlist","color","effect"):
            raise RuntimeError("Unsupported original mode: "+old)
        sessions.append((m,token,old))
    attempted=[]
    completed=False
    try:
        for m,token,old in sessions:
            attempted.append((m,old))
            checked(m["ip"],MODE,token,method="POST",payload={"mode":"rt"})
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            start=time.monotonic()
            phase=None
            duration=30 if a.effect=="diagnostic" else 10
            while (t:=time.monotonic()-start)<duration:
                if a.effect=="diagnostic":
                    current=0 if t<10 else 1 if t<20 else 2
                    if phase!=current:
                        print(["LEFT RED / RIGHT OFF","LEFT OFF / RIGHT GREEN","OPPOSING RAINBOWS"][current],flush=True)
                        phase=current
                    frame=rt.diagnostic_frame(members,t,a.channels,a.vertical_axis,a.invert_vertical)
                else:
                    frame=rt.frame_at(members,t,a.effect,a.channels)
                for m,token,old in sessions:
                    data=frame if a.transport=="master" else frame[m["offset"]*a.channels:(m["offset"]+m["leds"])*a.channels]
                    for packet in rt.packets(token,data):
                        sock.sendto(packet,(m["ip"],7777))
                time.sleep(.04)
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
