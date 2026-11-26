#!/usr/bin/env python3
"""Read-only cloud Area diagnostic. No controller requests or cloud writes."""
import argparse, datetime as dt, json, math
from pathlib import Path
import update_grouped_2d_cloud_v6 as v

def compare(label, actual, expected):
    try:
        print(label, 'max coordinate difference:', v.delta(actual, expected))
    except Exception as e:
        print(label, type(e).__name__, str(e))

def report(obj,bundle,corrected):
    old,_,target,_=bundle
    print('Identity matches original:',v.obj_identity(obj)==v.obj_identity(old))
    print('Cloud source:',obj['layout'].get('source'))
    print('Cloud aspect:',obj['layout'].get('aspectXY'),'target:',target['aspectXY'])
    actual=v.decode(obj['layout']); original=v.decode(old['layout']); wanted=v.decode(target)
    print('Cloud LED count:',len(actual))
    compare('Cloud vs original',actual,original)
    compare('Cloud vs corrected',actual,wanted)
    meta=json.loads(obj['meta']) if isinstance(obj['meta'],str) else obj['meta']
    print('Metadata exactly matches target:',meta==corrected)
    print('Metadata exactly matches original:',meta==json.loads(old['meta']))
    try:
        points,aspect=v.scene_points(meta)
        print('Editor reconstructed aspect:',aspect)
        compare('Editor vs corrected',points,wanted)
        compare('Editor vs cloud',points,actual)
    except Exception as e:
        print('Editor reconstruction failed:',type(e).__name__,str(e))
    def diff(a,b,path='meta'):
        if type(a)!=type(b): yield path+' (type)';return
        if isinstance(a,dict):
            for k in sorted(a.keys()|b.keys()):
                if k not in a or k not in b: yield path+'.'+k+' (missing)'
                else: yield from diff(a[k],b[k],path+'.'+k)
        elif isinstance(a,list):
            if len(a)!=len(b): yield path+' (length)'
            for i,(x,y) in enumerate(zip(a,b)): yield from diff(x,y,f'{path}[{i}]')
        elif a!=b: yield path
    changes=list(diff(meta,corrected));print('Metadata differing paths:',len(changes))
    for p in changes[:30]: print(' ',p)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',default='Front_2D_corrected_v3.zip');p.add_argument('--login',action='store_true');p.add_argument('--offline',action='store_true');a=p.parse_args()
    bundle=v.read_bundle(a.bundle);corrected=v.prepare(bundle);old=bundle[0]
    if a.offline:
        obj=dict(old);obj['layout']=bundle[2];obj['meta']=json.dumps(corrected);report(obj,bundle,corrected);return
    from cloud_group_live import get_cloud_token
    from cloud_audit import cloud_get, objects_from
    token=get_cloud_token(login=a.login,timeout=30)
    raw=cloud_get(v.SELECTOR,token,30)
    matches=[o for o in objects_from(raw) if str(o.get('id'))==str(old['id'])]
    if len(matches)!=1: raise ValueError('Expected exactly one Area object')
    obj=matches[0]
    out=Path('cloud-preflight-backups')/('cloud-diagnostic-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));out.mkdir(parents=True,exist_ok=False)
    v.save(out/'cloud-observed-private.json',obj)
    print('Saved current Area before validation:',out/'cloud-observed-private.json',flush=True)
    try: report(obj,bundle,corrected)
    finally: print('READ ONLY: no cloud PUT or controller requests.')
if __name__=='__main__': main()
