#!/usr/bin/env python3
"""Restore a verified multi-light Area after app ungrouping. Default is offline plan.
--preflight checks live standalone state; --execute requires that same preflight.
Never automatically retry or roll back a partial write.
"""
import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

from archive_area_group import read_archive, verify, capture
from archive_3d_maps import read_reference
from repair_area_members import refresh_member
from cloud_group_live import get_cloud_token
from cloud_group_preflight import mac, coordinates, compare_points, CLOUD_LOCAL_TOLERANCE
from cloud_audit import cloud_get, objects_from
from cloud_group_cycle_reverse import installation_id, cloud_fresh_standalones
from group_test import layout_matches
import cloud_group_cycle as ops
from twinkly_inspector import get_devices, request, validate_ip, save_json


def prepare(doc):
    area=doc["payload"]["area"]
    if doc.get("detached"):
        raise ValueError("Use a fully consistent Area archive")
    members=doc["payload"]["members"]
    if len(members)!=len(doc["macs_in_order"]):
        raise ValueError("Invalid archived member count")
    slots=sorted(area["devices"],key=lambda x:x["part"])
    offset=0
    for i,(m,s) in enumerate(zip(members,slots)):
        n=m["gestalt"]["number_of_led"]
        d=s["device"]
        if mac(d.get("mac"))!=doc["macs_in_order"][i] or d.get("ledCount")!=n:
            raise ValueError("Archived cloud descriptor identity/count mismatch")
        if any(k not in d for k in ops.DEVICE_KEYS):
            raise ValueError(m["name"]+": archived descriptor missing create fields")
        print(f"Part {i}: {m['name']} / {n} LEDs / offset {offset}")
        offset+=n
    if offset!=doc["led_count"]:
        raise ValueError("LED total mismatch")
    ops.create_body(area,slots,area["layout"],name=doc["group_name"],
                    installation_id=installation_id([area]),metadata=area["meta"])
    return area,members


def live_preflight(doc,area,members,args):
    token=get_cloud_token(timeout=args.timeout)
    inventory=objects_from(cloud_get(ops.CLOUD_SELECTOR,token,args.timeout))
    wanted=set(doc["macs_in_order"])
    found={}
    for obj in inventory:
        slots=obj.get("devices") or []
        addresses=[mac(s.get("device",{}).get("mac")) for s in slots]
        if not wanted.intersection(addresses):
            continue
        if len(slots)!=1 or addresses[0] in found:
            raise ValueError("Expected independent cloud objects; overlapping group or duplicate found")
        found[addresses[0]]=obj
    if set(found)!=wanted:
        raise ValueError("Not all archived members have independent cloud objects")
    standalone=[found[key] for key in doc["macs_in_order"]]
    installation=installation_id(standalone)
    if installation!=installation_id([area]):
        raise ValueError("Installation changed")
    ids=[o.get("id") for o in standalone]
    if any(type(x) is not int or x<=0 for x in ids) or len(set(ids))!=len(ids):
        raise ValueError("Invalid standalone object IDs")
    if any(str(o.get("name","")).casefold()==doc["group_name"].casefold() for o in inventory):
        raise ValueError("Cloud object already uses Area name")
    configured=get_devices(SimpleNamespace(config=args.config,ips=None))
    # Resolve current IP by physical MAC, not archived address or name.
    by_mac={}
    for d in configured:
        ip=validate_ip(d["ip"])
        result=request(ip,"/xled/v1/gestalt",timeout=args.timeout)
        if result.get("ok") and isinstance(result.get("data"),dict):
            key=mac(result["data"].get("mac"))
            if key in wanted:
                if key in by_mac:
                    raise ValueError("Duplicate live MAC")
                by_mac[key]=ip
    if set(by_mac)!=wanted:
        raise ValueError("Not all archived light sets are reachable")
    live=[]
    for key,m,obj in zip(doc["macs_in_order"],members,standalone):
        n=m["gestalt"]["number_of_led"]
        state=refresh_member({**m,"ip":by_mac[key]},args.timeout)
        ref=read_reference(args.maps,key,n)["layout"]
        if state["group"]["group"].get("mode")!="none" or not layout_matches(state["layout"],ref):
            raise ValueError(m["name"]+": standalone local 3D map does not match immutable reference")
        if obj.get("ledCount")!=n or obj["layout"].get("source")!="3d":
            raise ValueError(m["name"]+": invalid standalone cloud geometry")
        if not compare_points(coordinates(obj["layout"]),ref["coordinates"],tolerance=CLOUD_LOCAL_TOLERANCE)["matching"]:
            raise ValueError(m["name"]+": cloud/local 3D maps differ")
        d=obj["devices"][0]["device"]
        if d.get("ledCount")!=n or any(k not in d for k in ops.DEVICE_KEYS):
            raise ValueError(m["name"]+": incomplete live cloud descriptor")
        live.append(state)
        print("3D verified:",m["name"])
    return token,installation,standalone,live


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",required=True)
    p.add_argument("--config",default="devices.json")
    p.add_argument("--maps",default="3d-map-archive")
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--preflight",action="store_true")
    p.add_argument("--execute",action="store_true")
    p.add_argument("--confirm-group")
    p.add_argument("--acknowledge-risk",action="store_true")
    a=p.parse_args()
    journal=None
    dest=None
    try:
        if not 0<a.timeout<=30:
            raise ValueError("Invalid timeout")
        doc=read_archive(a.archive)
        area,members=prepare(doc)
        print("OFFLINE PLAN VERIFIED:",doc["group_name"],doc["led_count"],"LEDs")
        if not (a.preflight or a.execute):
            print("No writes. Offline plan only; cloud restore is not yet proven.")
            return 0
        if a.execute and (a.confirm_group!=doc["group_name"] or not a.acknowledge_risk):
            raise ValueError("Execution requires exact --confirm-group and --acknowledge-risk")
        token,installation,standalone,live=live_preflight(doc,area,members,a)
        print("LIVE PREFLIGHT PASS: all standalone cloud objects and original 3D maps verified")
        if not a.execute:
            return 0
        dest=Path("cloud-preflight-backups")/("multi-restore-"+dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        dest.mkdir(parents=True,exist_ok=False)
        save_json(dest/"area-private.json",doc)
        save_json(dest/"standalones-private.json",standalone)
        save_json(dest/"local-private.json",live)
        journal={"stages":[],"complete":False}
        def stage(label):
            journal["stages"].append(label)
            save_json(dest/"journal-private.json",journal)
            print(label,flush=True)
        uid=str(uuid.uuid4())
        layout=copy.deepcopy(area["layout"])
        layout["uuid"]=str(uuid.uuid4())
        descriptors=[{"part":i,"device":ops.device_payload(o["devices"][0]["device"],m["ip"],a.timeout)}
                     for i,(o,m) in enumerate(zip(standalone,live))]
        body=ops.create_body(area,descriptors,layout,name=doc["group_name"],installation_id=installation,metadata=area["meta"],object_uuid=uid)
        save_json(dest/"prepared-area-private.json",body)
        token=get_cloud_token(timeout=a.timeout)
        cloud_fresh_standalones(standalone,token,a.timeout)
        for m in live:
            now=refresh_member(m,a.timeout)
            if now["group"]["group"].get("mode")!="none" or not layout_matches(now["layout"],m["layout"]):
                raise ValueError(m["name"]+": local state changed since preflight")
        # Write master first, matching the previously tested Office procedure.
        offset=0
        for i,(m,saved) in enumerate(zip(live,members)):
            role="master" if i==0 else "slave"
            n=m["gestalt"]["number_of_led"]
            stage("Starting local writes: "+m["name"])
            ops.local_write(m,ops.LAYOUT_API,saved["layout"],a.timeout)
            ops.local_write(m,ops.GROUP_API,{"group":{"mode":role,"size":doc["led_count"],"offset":offset,"uid":uid,"compat_mode":saved["group"]["group"].get("compat_mode",0)}},a.timeout)
            state=refresh_member(m,a.timeout)
            g=state["group"]["group"]
            from cloud_group_preflight import normalized_uid
            if g.get("mode")!=role or g.get("size")!=doc["led_count"] or normalized_uid(g.get("uid"))!=normalized_uid(uid) or (g.get("offset") or 0)!=offset or not layout_matches(state["layout"],saved["layout"]):
                raise ValueError(m["name"]+": local write readback mismatch")
            stage("Local verified: "+m["name"])
            offset+=n
        token=get_cloud_token(timeout=a.timeout)
        cloud_fresh_standalones(standalone,token,a.timeout)
        for obj in standalone:
            stage("Detaching cloud standalone ID "+str(obj["id"]))
            ops.cloud_write("PUT",f"/v3/installations/{installation}/objects",{"action":"remove","objectIds":[obj["id"]]},token,200,a.timeout)
            inventory=objects_from(cloud_get(ops.CLOUD_SELECTOR,token,a.timeout))
            if any(o.get("id")==obj["id"] for o in inventory):
                raise ValueError("Standalone detachment not confirmed")
            stage("Detached "+str(obj["id"]))
        stage("Creating replacement cloud Area")
        reply=ops.cloud_write("POST","/v3/objects",body,token,201,a.timeout)
        stage("Created cloud Area ID "+str(reply.get("id")))
        inventory=objects_from(cloud_get(ops.CLOUD_SELECTOR,token,a.timeout))
        matches=[o for o in inventory if normalized_uid(o.get("uuid"))==normalized_uid(uid)]
        if len(matches)!=1:
            raise ValueError("New Area not uniquely found")
        restored=matches[0]
        current={key:refresh_member(m,a.timeout) for key,m in zip(doc["macs_in_order"],live)}
        _,order,detached=verify(restored,current)
        if order!=doc["macs_in_order"] or restored.get("name")!=doc["group_name"] or not compare_points(coordinates(restored["layout"]),coordinates(area["layout"]),tolerance=CLOUD_LOCAL_TOLERANCE)["matching"]:
            raise ValueError("Final Area differs from original archive")
        stage("Verified restored cloud and LAN geometry")
        capture(SimpleNamespace(group=doc["group_name"],config=a.config,timeout=a.timeout,login=False,root="2d_group_archive",allow_detached=False))
        journal["complete"]=True
        save_json(dest/"journal-private.json",journal)
        print("RESTORE PASS; check native app effects manually.")
        return 0
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        print("BLOCK:",exc,file=sys.stderr)
        if journal is not None:
            journal["error"]=str(exc)
            save_json(dest/"journal-private.json",journal)
            print("Partial writes possible. Do not rerun blindly. Recovery files:",dest,file=sys.stderr)
        return 2

if __name__=="__main__":
    sys.exit(main())
