#!/usr/bin/env python3
"""Read-only immutable cloud + LAN archive for an Area of any size."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

from archive_2d_group import checksum
from cloud_audit import cloud_get
from cloud_group_live import get_cloud_token
from cloud_group_preflight import mac, normalized_uid, objects_from, coordinates, compare_points, CLOUD_LOCAL_TOLERANCE
from twinkly_inspector import get_devices, request, authenticate, validate_ip

def checked(ip, path, timeout, token=None):
    result=request(ip,path,timeout=timeout,token=token)
    if not result.get("ok") or not isinstance(result.get("data"),dict):
        raise ValueError(f"{ip}: cannot read {path}: {result.get('error','invalid reply')}")
    return result["data"]

def verify(area, members, allow_detached=False):
    uid=normalized_uid(area.get("uuid"))
    if not uid or any(k not in area for k in ("capacity","fps","ledProfile","meta","uuid")):
        raise ValueError("Missing cloud Area creation metadata")
    layout=area.get("layout")
    if not isinstance(layout,dict) or any(k not in layout for k in ("coords","uuid","geometry","aspectXZ","aspectXY","name","synthesized")):
        raise ValueError("Missing cloud layout metadata")
    slots=area.get("devices",[])
    parts=[slot.get("part") for slot in slots]
    if any(type(part) is not int for part in parts) or sorted(parts)!=list(range(len(slots))):
        raise ValueError("Cloud Area parts must form a unique contiguous sequence")
    slots=sorted(slots,key=lambda slot:slot["part"])
    order=[mac(s.get("device",{}).get("mac")) for s in slots]
    if len(order)<2 or None in order or len(set(order))!=len(order) or set(order)!=set(members):
        raise ValueError("Cloud/local member identities differ")
    total=area.get("ledCount")
    offset=0
    points=[]
    detached=[]
    for i,address in enumerate(order):
        entry=members[address]
        g=entry["gestalt"]
        group=entry["group"]["group"]
        local=entry["layout"]
        n=g.get("number_of_led")
        if mac(g.get("mac"))!=address or type(n) is not int or n<=0:
            raise ValueError("Invalid hardware identity or LED count")
        role="master" if i==0 else "slave"
        observed_offset=group.get("offset")
        if observed_offset is None and i==0:
            observed_offset=0
        if (allow_detached and i>0 and group.get("mode")=="none"
                and group.get("uid") in (None,"") and group.get("size") is None
                and group.get("offset") is None):
            detached.append({"name":entry["name"],"mac":address,"part":i,"expected_offset":offset})
        elif normalized_uid(group.get("uid"))!=uid or group.get("mode")!=role or group.get("size")!=total or observed_offset!=offset:
            raise ValueError(f"{entry['name']}: expected UID={uid}, role={role}, "
                             f"size={total}, offset={offset}; observed "
                             f"UID={group.get('uid')}, role={group.get('mode')}, "
                             f"size={group.get('size')}, offset={observed_offset}")
        coords=local.get("coordinates")
        if local.get("source")!="2d" or type(local.get("synthesized")) is not bool or not isinstance(coords,list) or len(coords)!=n:
            raise ValueError(entry["name"]+": invalid local 2D map")
        points.extend(coords)
        offset+=n
    if total!=offset or not compare_points(coordinates(layout),points,tolerance=CLOUD_LOCAL_TOLERANCE)["matching"]:
        raise ValueError("Cloud/local geometry or LED total differs")
    return uid,order,detached

def capture(args, return_archive=False):
    token=get_cloud_token(login=args.login,timeout=args.timeout)
    inventory=objects_from(cloud_get("/v3/objects?mine=true&fields=layout,devices,capabilities",token,timeout=args.timeout))
    matches=[o for o in inventory if str(o.get("name","")).strip().casefold()==args.group.strip().casefold() and isinstance(o.get("devices"),list) and len(o["devices"])>1]
    if len(matches)!=1:
        raise ValueError("Expected one named cloud Area; available groups: "+", ".join(str(o.get("name")) for o in inventory if isinstance(o.get("devices"),list) and len(o["devices"])>1))
    area=matches[0]
    wanted={mac(s.get("device",{}).get("mac")) for s in area["devices"]}
    args.ips=None
    members={}
    for device in get_devices(args):
        ip=validate_ip(device["ip"])
        try:
            g=checked(ip,"/xled/v1/gestalt",args.timeout)
        except ValueError as exc:
            print("Unavailable:",device["name"],exc)
            continue
        address=mac(g.get("mac"))
        if address not in wanted:
            continue
        if address in members:
            raise ValueError("Duplicate hardware identity in config")
        local_token,error=authenticate(ip,args.timeout)
        if not local_token:
            raise ValueError(device["name"]+": authentication failed")
        members[address]={**device,"gestalt":g,
            "group":checked(ip,"/xled/v1/application/group",args.timeout,local_token),
            "layout":checked(ip,"/xled/v1/led/layout/full",args.timeout,local_token)}
    print("Cloud Area:",area.get("name"),"UID:",area.get("uuid"),"LEDs:",area.get("ledCount"))
    print("Cloud list order versus local group configuration:")
    for i,slot in enumerate(area["devices"]):
        address=mac(slot.get("device",{}).get("mac"))
        m=members.get(address)
        if m is None:
            print(f"  [{i}] Missing configured light set {str(address)[-4:]}")
            continue
        g=m["group"]["group"]
        print(f"  [{i}] {m['name']}: LEDs={m['gestalt'].get('number_of_led')}, "
              f"part={slot.get('part')}, role={g.get('mode')}, offset={g.get('offset')}, "
              f"size={g.get('size')}, UID={g.get('uid')}, layout={m['layout'].get('source')}")
    uid,order,detached=verify(area,members,args.allow_detached)
    payload={"area":area,"members":[members[a] for a in order]}
    dest=Path(args.root)/"multi-area"/uid/(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    dest.mkdir(parents=True,exist_ok=False)
    doc={"schema":2,"detached":detached,"group_name":area.get("name"),"uid":uid,"macs_in_order":order,
         "led_count":area["ledCount"],"payload":payload,"sha256":checksum(payload)}
    with (dest/"reference.json").open("x",encoding="utf-8") as f:
        json.dump(doc,f,indent=2,ensure_ascii=False,allow_nan=False)
    read_archive(dest/"reference.json")
    if detached:
        print("WARNING: detached local members:", ", ".join(d["name"] for d in detached))
        print("Geometry verified; membership is inconsistent. Restoration is not yet proven.")
    print("VERIFIED cloud and local geometry:",len(order),"light sets,",area["ledCount"],"LEDs")
    for address in order:
        m=members[address]
        print(m["name"],m["gestalt"]["number_of_led"],"LEDs")
    print("Private archive:",dest/"reference.json")
    return (doc, dest/"reference.json") if return_archive else 0

def read_archive(path):
    doc=json.loads(Path(path).read_text(encoding="utf-8"))
    payload=doc["payload"]
    if doc.get("schema") not in (1,2) or checksum(payload)!=doc.get("sha256"):
        raise ValueError("Archive checksum/schema mismatch")
    members={mac(m["gestalt"]["mac"]):m for m in payload["members"]}
    uid,order,detached=verify(payload["area"],members,doc["schema"]==2)
    if detached!=doc.get("detached",[]):
        raise ValueError("Archive detached membership manifest differs")
    if uid!=doc["uid"] or order!=doc["macs_in_order"] or doc["led_count"]!=payload["area"]["ledCount"]:
        raise ValueError("Archive manifest differs")
    return doc

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group")
    p.add_argument("--config",default="devices.json")
    p.add_argument("--root",default="2d_group_archive")
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--login",action="store_true")
    p.add_argument("--verify-archive")
    p.add_argument("--allow-detached",action="store_true",help="Archive explicitly ungrouped cloud members only if all geometry still matches")
    a=p.parse_args()
    try:
        if a.verify_archive:
            doc=read_archive(a.verify_archive)
            if doc.get("detached"):
                print("WARNING: detached local members:", ", ".join(d["name"] for d in doc["detached"]))
            print("VERIFIED:",doc["group_name"],len(doc["macs_in_order"]),"light sets,",doc["led_count"],"LEDs")
            return 0
        if not a.group or not 0<a.timeout<=30:
            p.error("Supply --group and timeout between 0 and 30 seconds")
        return capture(a)
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        print("BLOCK:",exc,file=sys.stderr)
        return 2

if __name__=="__main__":
    sys.exit(main())
