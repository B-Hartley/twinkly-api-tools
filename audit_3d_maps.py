#!/usr/bin/env python3
"""Audit all standalone 3D mappings against immutable references; optionally repair selected LAN maps only."""
import argparse
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import sys
from controller_workers import add_workers, controller_reads
from archive_3d_maps import read_reference, reference_path, archive, verify_layout
from cloud_group_preflight import mac, compare_points
from archive_area_group import checked
from cloud_group_cycle import local_write, LAYOUT_API, GROUP_API
from twinkly_inspector import get_devices, validate_ip, authenticate, save_json
from group_test import layout_matches

def read(m,timeout):
    ip=validate_ip(m["ip"])
    g=checked(ip,"/xled/v1/gestalt",timeout)
    key=mac(g.get("mac"))
    n=g.get("number_of_led")
    if not key or type(n) is not int or n<=0:
        raise ValueError("Invalid hardware identity")
    token,_=authenticate(ip,timeout)
    if not token:
        raise ValueError("LAN authentication failed")
    return key,n,checked(ip,GROUP_API,timeout,token)["group"],checked(ip,LAYOUT_API,timeout,token)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",default="devices.json")
    p.add_argument("--root",default="3d-map-archive")
    p.add_argument("--timeout",type=float,default=20)
    p.add_argument("--names",nargs="+")
    p.add_argument("--capture-missing",action="store_true")
    p.add_argument("--execute",action="store_true")
    add_workers(p)
    a=p.parse_args()
    if not 0<a.timeout<=30 or (a.execute and not a.names):
        p.error("Valid timeout and explicit --names required for writes")
    devices=get_devices(SimpleNamespace(config=a.config,ips=None))
    if a.names:
        devices=[m for m in devices if m["name"] in a.names]
        if sorted(m["name"] for m in devices)!=sorted(a.names):
            p.error("Names must match configured light sets exactly")
    failures=0
    plans=[]
    for m, result, error in controller_reads(devices, lambda m: read(m,a.timeout), a.workers):
        try:
            if error is not None: raise error
            key,n,group,layout=result
            if group.get("mode")!="none":
                raise ValueError("Still grouped; refusing standalone map operations")
            if not reference_path(a.root,key).exists():
                if layout.get("source")=="3d" and a.capture_missing:
                    archive(a.root,key,n,layout,m["name"],"captured-live-standalone-3d")
                else:
                    print(m["name"]+": NO ARCHIVE; live source="+str(layout.get("source")))
                    failures+=1
                    continue
            ref=read_reference(a.root,key,n)["layout"]
            if layout_matches(layout,ref):
                print(m["name"]+": MATCH")
            else:
                print(m["name"]+": RESTORE NEEDED; live source="+str(layout.get("source")))
                if layout.get("source")=="3d":
                    report=compare_points(ref["coordinates"],layout.get("coordinates",[]))
                    print("  Coordinate comparison:",report)
                plans.append((m,key,n,group,layout,ref))
        except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
            print(m["name"]+": BLOCK:",exc)
            failures+=1
    print("Summary:",len(plans),"maps differ;",failures,"missing or blocked")
    if not a.execute:
        print("No LAN or cloud writes.")
        return 2 if failures or plans else 0
    if failures:
        print("BLOCK: fix all selected preflight issues before writes.")
        return 2
    if not plans:
        return 0
    dest=Path(a.root)/"repair-backups"/dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest.mkdir(parents=True,exist_ok=False)
    for m,key,n,group,layout,ref in plans:
        save_json(dest/(key+".json"),{"name":m["name"],"mac":key,"led_count":n,"group":group,"layout":layout})
    print("Private pre-write backup:",dest)
    try:
        for m,key,n,group,layout,ref in plans:
            current=read(m,a.timeout)
            if current!=(key,n,group,layout):
                raise ValueError(m["name"]+": state changed; stop")
            ops_ref=verify_layout(ref,n)
            local_write(m,LAYOUT_API,ops_ref,a.timeout)
            k,c,g,l=read(m,a.timeout)
            if (k,c)!=(key,n) or g.get("mode")!="none" or not layout_matches(l,ref):
                raise ValueError(m["name"]+": readback failed")
            print(m["name"]+": RESTORED and verified locally")
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        print("STOP:",exc,"; partial writes possible; inspect before retry.",file=sys.stderr)
        return 2
    print("LAN only: cloud mappings have not been changed.")
    return 0

if __name__=="__main__":
    sys.exit(main())
