#!/usr/bin/env python3
"""Bounded Office group streaming experiment. Run on the Twinkly LAN."""
import argparse
import importlib.util
from pathlib import Path
import socket
import time
from group_test import load_plan
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
    p.add_argument("--plan",required=True)
    p.add_argument("--execute",action="store_true")
    p.add_argument("--effect",choices=["solid","rainbow"],default="rainbow")
    p.add_argument("--channels",type=int,choices=[3,4],required=True)
    p.add_argument("--transport",choices=["master","direct"],default="master")
    a=p.parse_args()
    plan,uid=load_plan(a.plan,require_verified=a.execute)
    members=sorted(plan["members"],key=lambda m:m["offset"])
    if len(members)!=2 or {m["name"].lower() for m in members}!={"office left","office right"}:
        raise ValueError("Office pair only")
    for m in members:
        if read_state(m,uid,plan["size"],5)!={"group":"expected","layout":"area"}:
            raise RuntimeError("Regroup and verify first")
    for m in members:
        print(m["name"],m["offset"],m["leds"])
    if not a.execute:
        print("Read-only preflight passed")
        return
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
    try:
        for m,token,old in sessions:
            attempted.append((m,old))
            checked(m["ip"],MODE,token,method="POST",payload={"mode":"rt"})
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            start=time.monotonic()
            while (t:=time.monotonic()-start)<10:
                frame=rt.frame_at(members,t,a.effect,a.channels)
                for m,token,old in sessions:
                    data=frame if a.transport=="master" else frame[m["offset"]*a.channels:(m["offset"]+m["leds"])*a.channels]
                    for packet in rt.packets(token,data):
                        sock.sendto(packet,(m["ip"],7777))
                time.sleep(.04)
    finally:
        errors=[]
        for m,old in reversed(attempted):
            try:
                token=login(m["ip"],5)
                checked(m["ip"],MODE,token,method="POST",payload={"mode":old})
                print("Restored",m["name"],old)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("Mode restoration failed: "+"; ".join(errors))
    for m in members:
        if read_state(m,uid,plan["size"],5)!={"group":"expected","layout":"area"}:
            raise RuntimeError("Post-test group/layout mismatch")
    print("Group/layout verified; visually confirm animation and restored mode")

if __name__=="__main__":
    main()
