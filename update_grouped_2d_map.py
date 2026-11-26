#!/usr/bin/env python3
"""Reconcile existing Twinkly Area scene, cloud coordinates and LAN 2D layouts.

Offline or read-only live preflight by default. No group, installation, movie,
mode or standalone 3D writes. Experimental live upload, explicitly confirmed.
Run from twinkly-api-tools root. Keep bundle and generated backups private.
"""
import argparse
import base64
import copy
import datetime as dt
import gzip
import json
import math
from pathlib import Path
import sys
import zipfile

TOL=0.0003
AXES='xyz'
SELECTOR='/v3/objects?mine=true&fields=layout,devices,capabilities'
PUT_KEYS=('name','uuid','ledCount','devices','ledProfile','fps','capacity','meta','palette','layout')

def require(ok,message):
    if not ok: raise ValueError(message)

def mac(s): return str(s).replace(':','').replace('-','').lower()
def decode(layout): return json.loads(gzip.decompress(base64.b64decode(layout['coords'],validate=True)))
def encode(points): return base64.b64encode(gzip.compress(json.dumps(points,separators=(',',':'),allow_nan=False).encode())).decode()
def delta(a,b):
    require(len(a)==len(b) and len(a)>0,'Coordinate count mismatch')
    d=[abs(float(x[k])-float(y[k])) for x,y in zip(a,b) for k in AXES]
    require(all(math.isfinite(v) for v in d),'Nonfinite coordinate')
    return max(d)

def scene_points(meta):
    require(meta['sceneType']=='area_editor' and meta['arrangement']=='scene','Unknown editor scene schema')
    raw=[]
    for entry in meta['scene']['layouts']:
        require(all(abs(entry['rotation'][k])<1e-8 for k in AXES),'Rotated scene not supported')
        a=float(entry['layout']['aspectRatio']); s=float(entry['scale']); t=entry['translate']
        require(a>0 and s>0 and abs(t['z'])<1e-8,'Unknown scene transform')
        w=s*min(1,a); h=s*min(1,1/a)
        for p in entry['layout']['coordinates']:
            raw.append(dict(x=t['x']+w*p['x']/2,y=t['y']+s/2-h*p['y'],z=p['z']))
    lo={k:min(p[k] for p in raw) for k in 'xy'}; hi={k:max(p[k] for p in raw) for k in 'xy'}
    w=hi['x']-lo['x']; h=hi['y']-lo['y']; require(w>0 and h>0,'Degenerate scene')
    return [dict(x=2*(p['x']-lo['x'])/w-1,y=(hi['y']-p['y'])/h,z=p['z']) for p in raw],w/h

def slots(obj): return [(mac(s['device']['mac']),s['device']['ledCount'],s['part']) for s in sorted(obj['devices'],key=lambda x:x['part'])]
def role(m):
    r=m['role']; return (r['mode'],r['uid'],r['size'],r.get('offset',0) if r['mode']=='master' else r['offset'],r.get('compat_mode'))
def member_identity(m): return (mac(m['mac']),m['count'],role(m))
def obj_identity(o): return (str(o['id']),o['name'],o['uuid'],o['ledCount'],slots(o),o['installation']['id'])
def read_bundle(path):
    with zipfile.ZipFile(path) as z:
        def get(n): return json.loads(z.read(n+'.json'))
        return get('original-cloud-object-private'),get('original-local-evidence-private'),get('corrected-cloud-layout-candidate'),get('corrected-local-evidence-candidate')

def prepare(bundle):
    old,oldlan,targetlayout,targetlan=bundle
    original=decode(old['layout']); target=decode(targetlayout); size=old['ledCount']
    require(old['layout']['source']==targetlayout['source']=='2d','Expected 2D Area')
    require(len(original)==len(target)==size,'Invalid total count')
    require(delta(original,oldlan['cloud_points'])<1e-7 and delta(target,targetlan['cloud_points'])<1e-7,'Bundle cloud coordinate mismatch')
    require(str(oldlan['cloud_id'])==str(targetlan['cloud_id'])==str(old['id']),'Object ID mismatch')
    require(all(old['layout'][k]==targetlayout[k] for k in old['layout'] if k not in ('coords','aspectXY')),'Unexpected cloud layout metadata change')
    meta=json.loads(old['meta']); flattened,aspect=scene_points(meta)
    require(delta(flattened,original)<1e-5 and abs(aspect-old['layout']['aspectXY'])<1e-4,'Original Area scene cannot be reproduced')
    print('Original editor scene reconstruction max error:',format(delta(flattened,original),'.9f'))
    corrected=copy.deepcopy(meta); entries=corrected['scene']['layouts']; members=targetlan['members']; before=oldlan['members']
    require(len(entries)==len(members)==len(before)==len(slots(old)),'Scene/member count mismatch')
    ar=float(targetlayout['aspectXY']); require(math.isfinite(ar) and ar>0,'Invalid target aspect')
    W=.9*min(1,ar); H=.9*min(1,1/ar); left=.5-W/2; top=.5-H/2
    offset=0
    for i,(e,m,b,s) in enumerate(zip(entries,members,before,slots(old))):
        n=m['count']; require(s==(mac(m['mac']),n,i) and mac(e['objectId'])==s[0],'Scene order / hardware identity mismatch')
        require(member_identity(m)==member_identity(b) and role(m)[3]==offset and role(m)[2]==size,'Role, offset or identity change')
        require(m['layout']['source']==b['layout']['source']=='2d','Local map not 2D')
        require(all(m['layout'][k]==b['layout'][k] for k in b['layout'] if k!='coordinates'),'Local layout metadata changed')
        chunk=target[offset:offset+n]; oldchunk=original[offset:offset+n]
        require(delta(chunk,m['layout']['coordinates'])<=TOL and delta(oldchunk,b['layout']['coordinates'])<=TOL,'Local and cloud map mismatch')
        require(all(abs(p['z']-q['z'])<=TOL for p,q in zip(chunk,oldchunk)),'Depth coordinate change')
        require(len(e['layout']['coordinates'])==n,'Source LED count mismatch')
        xs=[p['x'] for p in chunk]; ys=[p['y'] for p in chunk]; xl,xr=min(xs),max(xs); yb,yt=min(ys),max(ys)
        w=(xr-xl)*W/2; h=(yt-yb)*H; require(w>0 and h>0,'Degenerate member extent')
        scale=max(w,h); e['scale']=scale; e['translate']={'x':left+W*((xl+xr)/2+1)/2,'y':top+H*(1-yb)-scale/2,'z':0}
        e['layout']['aspectRatio']=w/h
        # These are editor-local COPIES, not independent camera-map archives.
        e['layout']['coordinates']=[dict(x=2*(p['x']-xl)/(xr-xl)-1,y=(p['y']-yb)/(yt-yb),z=p['z']) for p in chunk]
        offset+=n
    require(offset==size,'Incomplete member coverage')
    actual,newar=scene_points(corrected)
    require(delta(actual,target)<1e-7 and abs(newar-ar)<1e-6,'Corrected editor scene does not reproduce target')
    print('Corrected editor scene reconstruction max error:',format(delta(actual,target),'.9f'))
    print(f'{len(members)} controllers, {size} LEDs, aspect {ar:.5f}; group roles/offsets and LED order unchanged.')
    return corrected

def save(path,obj): path.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def live(a,bundle,corrected):
    from cloud_group_live import get_cloud_token
    from cloud_audit import cloud_get,objects_from
    from repair_uuid_case import original as repair
    from cloud_layout_update import put_object
    from cloud_group_cycle import local_write,LAYOUT_API
    from audit_3d_maps import read
    from archive_area_group import checked
    from twinkly_inspector import authenticate
    old,oldlan,targetlayout,targetlan=bundle
    a.group=old['name'];a.object_id=str(old['id']);token=get_cloud_token(login=a.login,timeout=a.timeout)
    def mode_movies(m):
        tok,_=authenticate(m['ip'],a.timeout); require(tok,'Local authentication failed')
        require(checked(m['ip'],'/xled/v1/led/mode',a.timeout,tok)['mode']=='off',m['name']+': must be off')
        require(checked(m['ip'],'/xled/v1/movies',a.timeout,tok)['movies']==[],m['name']+': movie catalogue must be empty')
    def current_cloud():
        raw=cloud_get(SELECTOR,token,a.timeout); objects=objects_from(raw)
        require(not isinstance(raw,dict) or (not raw.get('next_page') and (not isinstance(raw.get('count'),int) or raw['count']==len(objects))),'Incomplete cloud inventory')
        matches=[o for o in objects if str(o.get('id'))==a.object_id]; require(len(matches)==1,'Cloud Area missing')
        return matches[0]
    def check_cloud(o,expected,meta):
        require(obj_identity(o)==obj_identity(old),'Cloud membership/identity changed')
        require(o['layout']['source']=='2d' and delta(decode(o['layout']),expected)<=TOL,'Cloud geometry mismatch')
        require(abs(o['layout']['aspectXY']-(targetlayout['aspectXY'] if meta==corrected else old['layout']['aspectXY']))<1e-4,'Cloud aspect mismatch')
        m=json.loads(o['meta']); points,ar=scene_points(m)
        require(delta(points,expected)<=TOL and abs(ar-o['layout']['aspectXY'])<1e-4,'Cloud scene mismatch')
    missing,evidence,obj=repair.collect(a,token)
    require(not missing,'Group roles/UUID spelling inconsistent')
    require(obj_identity(obj)==obj_identity(old),'Cloud object differs from bundle')
    check_cloud(obj,decode(old['layout']),json.loads(old['meta']))
    for m,b in zip(evidence['members'],oldlan['members']):
        require(member_identity(m)==member_identity(b) and delta(m['layout']['coordinates'],b['layout']['coordinates'])<=TOL,'Live member differs from original bundle: '+m['name'])
        mode_movies(m)
    require(len(evidence['members'])==len(oldlan['members']),'Member count changed')
    payload={k:copy.deepcopy(obj[k]) for k in PUT_KEYS};payload['layout']['coords']=targetlayout['coords'];payload['layout']['aspectXY']=targetlayout['aspectXY'];payload['meta']=json.dumps(corrected,separators=(',',':'),allow_nan=False)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'); out=Path(a.output)/('area-map-'+stamp);out.mkdir(parents=True,exist_ok=False)
    save(out/'cloud-before-private.json',obj);save(out/'lan-before-private.json',evidence);save(out/'cloud-proposed-put-private.json',payload);save(out/'lan-target-private.json',targetlan)
    print('Private preflight / recovery evidence:',out)
    if not a.execute:
        print('PASS READ ONLY: no map, cloud, movie or group writes.');return
    require(input('Type UPDATE EXISTING AREA '+a.object_id+' to upload the maps: ').strip()=='UPDATE EXISTING AREA '+a.object_id,'Cancelled')
    check_cloud(current_cloud(),decode(old['layout']),json.loads(old['meta']))
    # No writes before all live identities, roles, maps and modes have passed again.
    def verify_member(m,expected):
        key,n,r,l=read(m,a.timeout)
        require(mac(key)==mac(m['mac']) and n==m['count'] and role({'role':r})==role(m),'Controller identity/role changed: '+m['name'])
        require(l['source']=='2d' and l['synthesized']==expected['synthesized'] and delta(l['coordinates'],expected['coordinates'])<=TOL,'Layout mismatch: '+m['name'])
    for m in evidence['members']: verify_member(m,m['layout']);mode_movies(m)
    journal=out/'journal.txt'
    def record(line):
        with journal.open('a',encoding='utf-8') as f: f.write(line+'\n');f.flush()
        print(line,flush=True)
    pairs=list(zip(evidence['members'],targetlan['members']))
    for m,t in pairs[1:]+pairs[:1]:
        verify_member(m,m['layout']);mode_movies(m)
        record('ATTEMPT LAN layout '+m['name'])
        target=t['layout'];local_write(m,LAYOUT_API,{k:copy.deepcopy(v) for k,v in target.items() if k!='code'},a.timeout)
        verify_member(m,target);record('VERIFIED LAN layout '+m['name'])
    for m,t in pairs: verify_member(m,t['layout']);mode_movies(m)
    check_cloud(current_cloud(),decode(old['layout']),json.loads(old['meta']))
    record('ATTEMPT cloud existing object PUT '+a.object_id)
    put_object(a.object_id,payload,token,a.timeout)
    after=current_cloud();check_cloud(after,decode(targetlayout),corrected);save(out/'cloud-after-private.json',after)
    missing,final,after=repair.collect(a,token);require(not missing,'Final roles differ');check_cloud(after,decode(targetlayout),corrected)
    for m,t in zip(final['members'],targetlan['members']): verify_member(m,t['layout']);mode_movies(m)
    save(out/'lan-after-private.json',final);record('PASS: editor scene, cloud and every LAN layout agree; group unchanged.')
    print('Refresh Twinkly app and inspect Area Editor before installing movies. HA saved group reference still needs replacing.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',required=True);p.add_argument('--offline',action='store_true');p.add_argument('--execute',action='store_true');p.add_argument('--login',action='store_true');p.add_argument('--config',default='devices.json');p.add_argument('--root',default='3d-map-archive');p.add_argument('--output',default='cloud-preflight-backups');p.add_argument('--timeout',type=float,default=20);a=p.parse_args()
    try:
        require(0<a.timeout<=30 and not(a.offline and a.execute),'Invalid mode or timeout');bundle=read_bundle(a.bundle);corrected=prepare(bundle)
        if a.offline: print('PASS OFFLINE: no network requests.');return 0
        live(a,bundle,corrected);return 0
    except Exception as e:
        print('STOP:',type(e).__name__,str(e),file=sys.stderr);print('If a write was attempted, partial changes are possible. Inspect private journal and backups; no automatic retry or rollback.',file=sys.stderr);return 2
if __name__=='__main__': sys.exit(main())
