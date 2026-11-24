#!/usr/bin/env python3
"""Repair detached local slaves to match a verified cloud Area; no cloud/layout writes."""
import argparse
import sys
from archive_area_group import capture, checked, verify
from cloud_group_live import get_cloud_token
from cloud_audit import cloud_get
from cloud_group_preflight import mac, normalized_uid, objects_from, coordinates, compare_points, CLOUD_LOCAL_TOLERANCE
from cloud_group_cycle import local_write, GROUP_API
from twinkly_inspector import authenticate

SELECTOR="/v3/objects?mine=true&fields=layout,devices,capabilities"

def refresh_member(member,timeout):
    ip=member["ip"]
    g=checked(ip,"/xled/v1/gestalt",timeout)
    if mac(g.get("mac"))!=mac(member["gestalt"].get("mac")) or g.get("number_of_led")!=member["gestalt"].get("number_of_led"):
        raise ValueError(member["name"]+": hardware changed")
    token,_=authenticate(ip,timeout)
    if not token:
        raise ValueError(member["name"]+": LAN authentication failed")
    return {**member,"gestalt":g,"group":checked(ip,GROUP_API,timeout,token),
            "layout":checked(ip,"/xled/v1/led/layout/full",timeout,token)}

def fresh_area(area,timeout):
    objects=objects_from(cloud_get(SELECTOR,get_cloud_token(timeout=timeout),timeout))
    matches=[o for o in objects if o.get("id")==area.get("id") and normalized_uid(o.get("uuid"))==normalized_uid(area.get("uuid"))]
    if len(matches)!=1:
        raise ValueError("Cloud Area changed")
    fresh=matches[0]
    def parts(obj):
        return sorted((s["part"],mac(s["device"]["mac"])) for s in obj["devices"])
    if fresh.get("ledCount")!=area.get("ledCount") or parts(fresh)!=parts(area) or not compare_points(coordinates(fresh["layout"]),coordinates(area["layout"]),tolerance=CLOUD_LOCAL_TOLERANCE)["matching"]:
        raise ValueError("Cloud Area membership or geometry changed")
    return fresh

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group",required=True)
    p.add_argument("--config",default="devices.json")
    p.add_argument("--root",default="2d_group_archive")
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--login",action="store_true")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--confirm-group")
    p.add_argument("--members",nargs="+",required=True,help="Exact names of detached light sets expected")
    a=p.parse_args()
    a.allow_detached=True
    try:
        if not 0<a.timeout<=30 or (a.execute and a.confirm_group!=a.group):
            raise ValueError("Invalid timeout or missing exact --confirm-group")
        doc,path=capture(a,return_archive=True)
        targets=doc["detached"]
        if not targets:
            print("No detached members; nothing to repair.")
            return 0
        if sorted(d["name"] for d in targets)!=sorted(a.members):
            raise ValueError("Detached members differ from explicit --members")
        area=doc["payload"]["area"]
        members={mac(m["gestalt"]["mac"]):m for m in doc["payload"]["members"]}
        for d in targets:
            print("PLAN:",d["name"],"slave, offset",d["expected_offset"],"size",doc["led_count"])
        if not a.execute:
            print("DRY RUN: no changes made. Verified backup:",path)
            return 0
        # Refresh all state before writes, and ensure only the expected detached members remain.
        area=fresh_area(area,a.timeout)
        members={key:refresh_member(m,a.timeout) for key,m in members.items()}
        uid,order,observed=verify(area,members,True)
        if observed!=targets:
            raise ValueError("Detached member list changed since backup")
        master=members[order[0]]["group"]["group"]
        compat=master.get("compat_mode",0)
        if type(compat) is not int:
            raise ValueError("Unexpected master compatibility mode")
        for d in targets:
            area=fresh_area(area,a.timeout)
            key=d["mac"]
            members[key]=refresh_member(members[key],a.timeout)
            _,_,remaining=verify(area,members,True)
            if d not in remaining:
                raise ValueError("Target membership changed before write")
            print("Writing local group only:",d["name"],flush=True)
            local_write(members[key],GROUP_API,{"group":{"mode":"slave","size":doc["led_count"],"offset":d["expected_offset"],"uid":uid,"compat_mode":compat}},a.timeout)
            members[key]=refresh_member(members[key],a.timeout)
            _,_,after=verify(area,members,True)
            if d in after:
                raise ValueError("Target did not join; stop without retry")
            print("Verified local membership and geometry:",d["name"])
        area=fresh_area(area,a.timeout)
        members={key:refresh_member(m,a.timeout) for key,m in members.items()}
        verify(area,members,False)
        print("PASS: all local members and coordinates match cloud. Capturing final archive.")
        a.allow_detached=False
        return capture(a)
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        print("BLOCK:",exc,file=sys.stderr)
        print("If a write occurred, inspect current state before retrying.",file=sys.stderr)
        return 2

if __name__=="__main__":
    sys.exit(main())
