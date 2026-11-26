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
import http.client
import time
import urllib.error
import urllib.request
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

def fixed_json(value):
    """Encode floats without exponent notation for the LAN firmware parser."""
    from decimal import Decimal
    if type(value) is float:
        require(math.isfinite(value), 'Nonfinite JSON number')
        return format(Decimal(repr(value)), 'f')
    if isinstance(value, dict):
        return '{'+','.join(json.dumps(k)+':'+fixed_json(v) for k,v in value.items())+'}'
    if isinstance(value, list):
        return '['+','.join(fixed_json(v) for v in value)+']'
    return json.dumps(value, separators=(',',':'), allow_nan=False)

class PacedHTTP:
    """One synchronous request at a time, with a quiet period after each response.

    No automatic retries, including authentication and writes. Log only safe
    request metadata, never authentication tokens, response bodies or payloads.
    """
    def __init__(self, delay, timeout, log, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.delay=delay; self.timeout=timeout; self.log=log
        self.clock=clock; self.sleep=sleep; self.last_end=None; self.tokens={}
        if opener is None:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):
                    raise RuntimeError('Unexpected LAN redirect refused')
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open
        self.opener=opener

    def pace(self):
        if self.last_end is not None:
            remaining=self.delay-(self.clock()-self.last_end)
            if remaining>0: self.sleep(remaining)

    def external(self,label,operation):
        self.pace(); self.log('BEGIN '+label); start=self.clock()
        try:
            result=operation()
        except Exception as exc:
            self.log(f'FAIL {label} after {self.clock()-start:.2f}s: {type(exc).__name__}')
            raise
        else:
            self.log(f'OK {label} in {self.clock()-start:.2f}s'); return result
        finally:
            self.last_end=self.clock()

    def request(self,m,path,method='GET',payload=None,token=None):
        headers={'Accept':'application/json','Connection':'close'}
        if token: headers['X-Auth-Token']=token
        body=None
        if payload is not None:
            body=(fixed_json(payload) if path=='/xled/v1/led/layout/full' else json.dumps(payload,separators=(',',':'),allow_nan=False)).encode('utf-8')
            headers['Content-Type']='application/json'
        label=f"{m['name']} ({m['ip']}) {method} {path}"
        def fetch():
            req=urllib.request.Request('http://'+m['ip']+path,data=body,headers=headers,method=method)
            with self.opener(req,timeout=self.timeout) as response:
                raw=response.read(8*1024*1024+1)
                require(len(raw)<=8*1024*1024,'Response too large')
                # Some controllers incorrectly terminate a body at a fixed size.
                # Never accept an incomplete or invalid JSON response.
                expected=response.headers.get('Content-Length')
                if expected is not None and response.headers.get('Transfer-Encoding') is None:
                    require(len(raw)==int(expected),'Incomplete response body')
                result=json.loads(raw)
                require(isinstance(result,dict),'Expected JSON object')
                self.log(f'  HTTP {response.status}, received {len(raw)} bytes, API code {result.get("code")}')
                require(result.get('code') in (None,1000),'Controller rejected request')
                if method=='POST' and path=='/xled/v1/led/layout/full':
                    require(result.get('code')==1000,'Layout write response ambiguous')
                return result
        return self.external(label,fetch)

    def auth(self,m):
        import os
        ip=m['ip']; cached=self.tokens.get(ip)
        if cached and self.clock()<cached[1]: return cached[0]
        start=self.clock()
        data=self.request(m,'/xled/v1/login','POST',{'challenge':base64.b64encode(os.urandom(32)).decode('ascii')})
        token=data.get('authentication_token'); challenge=data.get('challenge-response')
        require(isinstance(token,str) and token and isinstance(challenge,str) and challenge,'Authentication response incomplete')
        self.request(m,'/xled/v1/verify','POST',{'challenge-response':challenge},token)
        expiry=data.get('authentication_token_expires_in',60)
        if type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry<=0: expiry=60
        self.tokens[ip]=(token,start+max(0,min(expiry-10,600)))
        return token

    def get(self,m,path,authenticated=True):
        return self.request(m,path,token=self.auth(m) if authenticated else None)

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
    from cloud_layout_update import put_object
    from archive_3d_maps import read_reference
    from twinkly_inspector import get_devices
    from types import SimpleNamespace
    old,oldlan,targetlayout,targetlan=bundle
    a.group=old['name'];a.object_id=str(old['id'])
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=Path(a.output)/('area-map-'+stamp);out.mkdir(parents=True,exist_ok=False)
    journal=out/'journal.txt'
    def record(line):
        line=dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')+' '+line
        with journal.open('a',encoding='utf-8') as f: f.write(line+'\n');f.flush()
        print(line,flush=True)
    print('Private diagnostic journal:',journal,flush=True)
    record(f'LAN request gap {a.delay}s; LAN timeout {a.timeout}s; no automatic retries')
    transport=PacedHTTP(a.delay,a.timeout,record)
    cloud_timeout=min(a.timeout,30)
    token=transport.external('cloud session authentication',lambda:get_cloud_token(login=a.login,timeout=cloud_timeout))
    configured={m['ip']:m for m in get_devices(SimpleNamespace(config=a.config,ips=None))}
    members=oldlan['members']
    for m in members:
        require(m['ip'] in configured and configured[m['ip']]['name']==m['name'],'Configured controller differs: '+m['name'])
        read_reference(a.root,mac(m['mac']),m['count'])
    def current_cloud():
        raw=transport.external('GET cloud Area inventory',lambda:cloud_get(SELECTOR,token,cloud_timeout));objects=objects_from(raw)
        require(not isinstance(raw,dict) or (not raw.get('next_page') and (not isinstance(raw.get('count'),int) or raw['count']==len(objects))),'Incomplete cloud inventory')
        matches=[o for o in objects if str(o.get('id'))==a.object_id];require(len(matches)==1,'Cloud Area missing')
        obj=matches[0]; wanted={x[0] for x in slots(old)}
        for other in objects:
            if other is obj: continue
            require(not any(mac((s.get('device') or {}).get('mac')) in wanted for s in (other.get('devices') or [])),'Member also belongs to another cloud object')
        return obj
    def check_cloud(o,expected,meta):
        require(obj_identity(o)==obj_identity(old),'Cloud membership/identity changed')
        require(o['layout']['source']=='2d' and delta(decode(o['layout']),expected)<=TOL,'Cloud geometry mismatch')
        require(abs(o['layout']['aspectXY']-(targetlayout['aspectXY'] if meta==corrected else old['layout']['aspectXY']))<1e-4,'Cloud aspect mismatch')
        actual=json.loads(o['meta']);points,ar=scene_points(actual)
        scene_members=[(mac(e['objectId']),len(e['layout']['coordinates'])) for e in actual['scene']['layouts']]
        require(scene_members==[(x[0],x[1]) for x in slots(o)],'Cloud scene membership/order changed')
        require(delta(points,expected)<=TOL and abs(ar-o['layout']['aspectXY'])<1e-4,'Cloud scene mismatch')
        require(actual==meta,'Cloud editor metadata changed')
    def verify_member(m,expected=None,check_movies=True):
        g=transport.get(m,'/xled/v1/gestalt',False)
        r=transport.get(m,'/xled/v1/application/group')['group']
        require(mac(g['mac'])==mac(m['mac']) and g['number_of_led']==m['count'] and role({'role':r})==role(m),'Controller identity/role changed: '+m['name'])
        result=copy.deepcopy(m);result['role']=r
        if check_movies:
            require(transport.get(m,'/xled/v1/led/mode')['mode']=='off',m['name']+': must be off')
            require(transport.get(m,'/xled/v1/movies')['movies']==[],m['name']+': movie catalogue must be empty')
        if expected is not None:
            l=transport.get(m,'/xled/v1/led/layout/full')
            require(l['source']=='2d' and l['synthesized']==expected['synthesized'] and delta(l['coordinates'],expected['coordinates'])<=TOL,'Layout mismatch: '+m['name'])
            result['layout']=l
        record('PASS '+m['name']+(' identity, group and geometry' if expected is not None else ' identity and group')+(', off and empty movies' if check_movies else ''))
        return result
    def expected_before(m):
        if m['name'] != a.recovery_member:
            return m['layout']
        return recovery
    recovery=None
    if a.recovery_member:
        matches=[t for t in targetlan['members'] if t['name']==a.recovery_member]
        require(len(matches)==1 and a.recovery_layout,'Exact recovery member and observed layout file required')
        recovery=json.loads(Path(a.recovery_layout).read_text(encoding='utf-8'))
        expected=matches[0]['layout']
        require(recovery['source']==expected['source'] and recovery['synthesized']==expected['synthesized'] and recovery['uuid']==expected['uuid'],'Recovery metadata mismatch')
        require(len(recovery['coordinates'])==len(expected['coordinates']),'Recovery LED count mismatch')
        anomalies=[]
        for i,(p,q) in enumerate(zip(recovery['coordinates'],expected['coordinates'])):
            for k in AXES:
                require(math.isfinite(p[k]) and math.isfinite(q[k]),'Nonfinite recovery coordinate')
                if abs(p[k]-q[k])>TOL:
                    require('e' in repr(q[k]).lower(),'Recovery differs outside scientific-notation coordinate')
                    anomalies.append((i,k))
        require(len(anomalies)==1,'Expected exactly one scientific-notation anomaly')
        record('Explicit recovery candidate: '+a.recovery_member+'; anomalous coordinate '+str(anomalies[0]))
    else:
        require(not a.recovery_layout,'Recovery member required')
    def proposed(obj):
        payload={k:copy.deepcopy(obj[k]) for k in PUT_KEYS};payload['layout']['coords']=targetlayout['coords'];payload['layout']['aspectXY']=targetlayout['aspectXY'];payload['meta']=json.dumps(corrected,separators=(',',':'),allow_nan=False)
        return payload
    oldmeta=json.loads(old['meta']);oldpoints=decode(old['layout']);targetpoints=decode(targetlayout)
    obj=current_cloud();check_cloud(obj,oldpoints,oldmeta)
    evidence={'cloud_id':obj['id'],'cloud_points':decode(obj['layout']),'members':[]}
    for m in members: evidence['members'].append(verify_member(m,expected_before(m)))
    payload=proposed(obj)
    save(out/'cloud-before-private.json',obj);save(out/'lan-before-private.json',evidence);save(out/'cloud-proposed-put-private.json',payload);save(out/'lan-target-private.json',targetlan)
    record('PASS: all 15 controller maps, roles, off mode and empty catalogues verified; recovery files saved')
    if not a.execute:
        record('PASS READ ONLY: no map, cloud, movie or group writes.');return
    require(input('Type UPDATE EXISTING AREA '+a.object_id+' to upload the maps: ').strip()=='UPDATE EXISTING AREA '+a.object_id,'Cancelled')
    record('READ ONLY: rechecking cloud and all controller maps after confirmation')
    fresh=current_cloud();check_cloud(fresh,oldpoints,oldmeta)
    for m in members: verify_member(m,expected_before(m))
    record('PASS: all post-confirmation read-only checks completed')
    pairs=list(zip(members,targetlan['members']))
    for m,t in pairs[1:]+pairs[:1]:
        # A full old layout was checked in the post-confirmation pass. Avoid
        # downloading it a third time. Recheck identity, group and off/empty
        # immediately before the write; verify the full new map afterwards.
        record('READ ONLY: immediate pre-write identity/mode check '+m['name'])
        verify_member(m)
        record('ATTEMPT LAN layout '+m['name'])
        transport.request(m,'/xled/v1/led/layout/full','POST',{k:copy.deepcopy(v) for k,v in t['layout'].items() if k!='code'},transport.auth(m))
        verify_member(m,t['layout']);record('VERIFIED LAN layout '+m['name'])
    fresh=current_cloud();check_cloud(fresh,oldpoints,oldmeta)
    payload=proposed(fresh);save(out/'cloud-final-put-private.json',payload)
    record('ATTEMPT cloud existing object PUT '+a.object_id)
    transport.external('PUT cloud Area '+a.object_id,lambda:put_object(a.object_id,payload,token,cloud_timeout))
    after=current_cloud();check_cloud(after,targetpoints,corrected);save(out/'cloud-after-private.json',after)
    final={'cloud_id':after['id'],'cloud_points':decode(after['layout']),'members':[]}
    for m,t in pairs: final['members'].append(verify_member(m,t['layout']))
    save(out/'lan-after-private.json',final);record('PASS: editor scene, cloud and every LAN layout agree; group unchanged.')
    print('Refresh Twinkly app and inspect Area Editor before installing movies. HA saved group reference still needs replacing.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',required=True);p.add_argument('--offline',action='store_true');p.add_argument('--execute',action='store_true');p.add_argument('--login',action='store_true');p.add_argument('--config',default='devices.json');p.add_argument('--root',default='3d-map-archive');p.add_argument('--output',default='cloud-preflight-backups');p.add_argument('--timeout',type=float,default=45,help='LAN socket timeout in seconds');p.add_argument('--delay',type=float,default=2,help='Minimum quiet period between HTTP requests in seconds');p.add_argument('--recovery-member');p.add_argument('--recovery-layout');a=p.parse_args()
    try:
        require(0<a.timeout<=120 and 0<=a.delay<=30 and not(a.offline and a.execute),'Invalid mode, timeout or request delay');bundle=read_bundle(a.bundle);corrected=prepare(bundle)
        if a.offline: print('PASS OFFLINE: no network requests.');return 0
        live(a,bundle,corrected);return 0
    except Exception as e:
        print('STOP:',type(e).__name__,str(e),file=sys.stderr);print('Inspect the journal for ATTEMPT LAN layout or ATTEMPT cloud entries. If present, partial changes are possible; no automatic retry or rollback.',file=sys.stderr);return 2
if __name__=='__main__': sys.exit(main())
