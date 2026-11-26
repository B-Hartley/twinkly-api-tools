#!/usr/bin/env python3
"""Private Front movie installer. Default is read-only, never deletes or changes maps/mode."""
import argparse, base64, hashlib, ipaddress, json, os, sys, time, urllib.request, urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
_last_request = 0.0

def call(ip,endpoint,timeout,token=None,payload=None):
    global _last_request
    ip=ipaddress.ip_address(ip)
    if ip.version!=4 or not ip.is_private or ip.is_loopback or ip.is_link_local: raise ValueError('Expected private LAN IPv4 address')
    headers={'Accept':'application/json','Connection':'close'}
    if token: headers['X-Auth-Token']=token
    body=None
    if payload is not None:
        binary=isinstance(payload,bytes);body=payload if binary else json.dumps(payload).encode();headers['Content-Type']='application/octet-stream' if binary else 'application/json'
    req=urllib.request.Request(f'http://{ip}/xled/v1/{endpoint}',data=body,headers=headers)
    time.sleep(max(0, 2.0 - (time.monotonic() - _last_request)))
    started=time.monotonic()
    print(f'{ip} {req.get_method()} /xled/v1/{endpoint} ({len(body or b"")} request bytes)',flush=True)
    try:
        with OPENER.open(req,timeout=timeout) as response:
            raw=response.read(1024*1024+1)
            print(f'  HTTP {response.status}, {len(raw)} bytes, {time.monotonic()-started:.2f}s',flush=True)
    except urllib.error.HTTPError as exc:
        detail=exc.read(8192).decode('utf-8',errors='replace') if endpoint in ('movies/new','movies/full') else '(body omitted)'
        raise RuntimeError(f'{ip} {endpoint}: HTTP {exc.code}: {detail}') from exc
    except OSError as exc:
        raise RuntimeError(f'{ip} {endpoint}: {type(exc).__name__}: {exc}') from exc
    finally:
        _last_request=time.monotonic()
    if len(raw)>1024*1024: raise ValueError('Oversized API reply')
    try: d=json.loads(raw)
    except ValueError:
        s=raw.decode().lstrip();d,end=json.JSONDecoder().raw_decode(s)
        if s[end:].strip().lower()!='connection: close': raise
    if not isinstance(d,dict) or d.get('code')!=1000: raise ValueError(f'{endpoint}: rejected (code {d.get("code") if isinstance(d,dict) else "unknown"})')
    return d

def authenticate(ip,timeout):
    d=call(ip,'login',timeout,payload={'challenge':base64.b64encode(os.urandom(32)).decode()})
    t=d['authentication_token'];call(ip,'verify',timeout,t,{'challenge-response':d['challenge-response']});return t

def mac(s): return s.lower().replace(':','').replace('-','')
def inspect(m,timeout):
    ip=m['ip']; g=call(ip,'gestalt',timeout);t=authenticate(ip,timeout)
    if mac(g['mac'])!=m['mac'] or g['number_of_led']!=m['count'] or g.get('led_profile')!='RGBW':
        raise ValueError(f"{m['name']}: identity, count or RGBW format changed; actual profile={g.get('led_profile')}")
    group=call(ip,'application/group',timeout,t)['group'];off=group.get('offset')
    if off is None and group.get('mode')=='master':off=0
    if (group.get('uid')!=m['group_uid'] or group.get('size')!=4900 or group.get('mode')!=m['role'] or off!=m['offset']):raise ValueError(f"{m['name']}: live group UUID, role, size or offset differs")
    mode=call(ip,'led/mode',timeout,t)['mode'];cat=call(ip,'movies',timeout,t)
    if not isinstance(cat.get('movies'),list) or type(cat.get('available_frames')) is not int or type(cat.get('max')) is not int:raise ValueError('Unknown capacity')
    return {'mode':mode,'catalogue':cat},t

def read_bundle(names):
    p=json.loads((ROOT/'manifest.json').read_text());members=p['controllers'];movies=[m for m in p['movies'] if not names or m['slug'] in names]
    if names and set(names)!={m['slug'] for m in movies}:raise ValueError('Unknown movie slug')
    if len(members)!=15 or len({m['mac'] for m in members})!=15 or len({m['ip'] for m in members})!=15 or len({m['group_uid'] for m in members})!=1 or sum(m['role']=='master' for m in members)!=1:raise ValueError('Incomplete group')
    offset=0
    for m in sorted(members,key=lambda x:x['offset']):
        if m['offset']!=offset or m['width']!=4 or m['count']<=0 or m['role'] not in ('master','slave'):raise ValueError('Invalid wire layout')
        offset+=m['count']
    if offset!=4900:raise ValueError('Wrong group size')
    jobs={}
    for movie in movies:
        if len(movie['name'].encode('utf-8')) > 16: raise ValueError('Movie name exceeds conservative 16-byte limit')
        if len(movie['jobs'])!=15 or {j['mac'] for j in movie['jobs']}!={m['mac'] for m in members}:raise ValueError('Missing member movie')
        for j in movie['jobs']:
            m=next(m for m in members if m['mac']==j['mac']);path=(ROOT/j['file']).resolve()
            if not path.is_relative_to(ROOT):raise ValueError('Unexpected path')
            b=path.read_bytes();meta=j['metadata'];expected={'name':movie['name'],'unique_id':movie['unique_id'],'descriptor_type':'rgbw_raw','leds_per_frame':m['count'],'frames_number':movie['frames'],'fps':movie['fps']}
            if meta!=expected or len(b)!=j['bytes'] or len(b)!=movie['frames']*m['count']*4 or hashlib.sha256(b).hexdigest()!=j['sha256']:raise ValueError(f'Corrupt file: {path.name}')
            jobs[movie['slug'],m['mac']]=(meta,b)
    return members,movies,jobs

def capacity(cat,movies):
    if len(cat['movies'])+len(movies)>cat['max'] or cat['available_frames']<sum(m['frames'] for m in movies):raise ValueError('Not enough available movie slots or frames')
    for existing in cat['movies']:
        if any(existing.get('name')==m['name'] or str(existing.get('unique_id','')).lower()==m['unique_id'].lower() for m in movies):raise ValueError('Movie name or UUID already present; refusing duplicates/partial retry')

def matching_entry(cat, meta):
    candidates = [x for x in cat['movies'] if x.get('name') == meta['name']
                  or str(x.get('unique_id','')).lower() == meta['unique_id'].lower()]
    if not candidates:
        return False
    if len(candidates) != 1 or not all(
        str(candidates[0].get(k,'')).lower() == v.lower() if k == 'unique_id'
        else candidates[0].get(k) == v for k,v in meta.items()):
        raise ValueError('Conflicting movie name/UUID or metadata: ' + meta['name'])
    return True

JOURNAL = ROOT / 'private-movie-upload-journal.jsonl'

def journal_key(member, meta, data):
    return (member['group_uid'].lower(), member['mac'], meta['unique_id'].lower(),
            hashlib.sha256(data).hexdigest())

def read_journal():
    records = {}
    if JOURNAL.exists():
        for line in JOURNAL.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            records[tuple(row['key'])] = row['stage']
    return records

def record_stage(records, key, stage):
    with JOURNAL.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'key': key, 'stage': stage, 'time': time.time()})+'\n')
        f.flush()
        os.fsync(f.fileno())
    records[key] = stage

def pending_movies(member, cat, movies, jobs, records, resume, accept_existing):
    pending = []
    for movie in movies:
        meta,data = jobs[movie['slug'],member['mac']]
        key = journal_key(member,meta,data)
        present = matching_entry(cat,meta)
        stage = records.get(key)
        if not present:
            pending.append(movie)
        elif not resume:
            raise ValueError('Movie already present; use --resume')
        elif stage in ('attempt','created'):
            raise ValueError('Existing entry may have incomplete binary transfer: '+meta['name'])
        elif stage not in ('binary-ok','verified') and not accept_existing:
            raise ValueError('Existing metadata has no transfer journal; use --accept-existing only with prior successful upload evidence')
    capacity(cat,pending)
    return pending

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--movie',action='append',choices=['bush-window-v2','window-chase','house-garden','bush-startup','window-startup']);p.add_argument('--timeout',type=float,default=45);p.add_argument('--resume',action='store_true');p.add_argument('--accept-existing',action='store_true',help='Trust matching legacy catalogue entries only when prior binary upload success is known');p.add_argument('--execute',action='store_true');p.add_argument('--verify-files',action='store_true');a=p.parse_args()
    members,movies,jobs=read_bundle(a.movie);frames=sum(m['frames'] for m in movies)
    print(f'Validated {len(jobs)} raw files; {len(movies)} movies, {frames} frames per controller.')
    if a.verify_files:return
    if a.accept_existing and not a.resume: raise ValueError('--accept-existing requires --resume')
    records=read_journal(); snapshots={};issues=[]; pending={}
    for m in members:
        try:
            state,_=inspect(m,a.timeout); snapshots[m['mac']]=state;cat=state['catalogue']; print(f"{m['name']:24s} free={cat['available_frames']:5d} slots={cat['max']-len(cat['movies']):2d} mode={state['mode']}")
            todo=pending_movies(m,cat,movies,jobs,records,a.resume,a.accept_existing)
            pending[m['mac']]={movie['slug'] for movie in todo}
            print(f"  {len(todo)} missing, {len(movies)-len(todo)} already present",flush=True)
            if state['mode']!='off':raise ValueError('Controller must be off')
        except Exception as e:issues.append(f"{m['name']}: {e}")
    if issues:raise ValueError('Preflight failed; NO movie writes attempted:\n'+'\n'.join(issues))
    print('PASS: all 15 controllers, group roles/offsets/UUID, capacity and off mode checked.')
    if not a.execute: print('Read-only preflight. No writes except authentication.');return
    if not any(pending.values()): print('All selected movie catalogues already match. No writes.');return
    if input('Type UPLOAD FRONT to attempt additive movie upload: ')!='UPLOAD FRONT':return
    for m in members:
        state,_=inspect(m,a.timeout)
        if state!=snapshots[m['mac']]:raise ValueError('Preflight changed; NO movie writes attempted')
    print('Beginning experimental uploads. Do not interrupt or use Twinkly/HA during upload.',flush=True)
    for movie in movies:
        print('Movie:',movie['name'],flush=True)
        for m in sorted(members,key=lambda x:x['role']=='master'):
            before,t=inspect(m,a.timeout);k=m['mac']
            if before!=snapshots[k]:raise ValueError(f"{m['name']}: live state changed; stop")
            meta,data=jobs[movie['slug'],k]; key=journal_key(m,meta,data)
            if movie['slug'] not in pending[k]:
                if not matching_entry(before['catalogue'],meta): raise ValueError('Previously present movie disappeared')
                print('  skipped existing',m['name'],flush=True)
                continue
            capacity(before['catalogue'],[movie])
            print(f"  Uploading {m['name']}: {meta!r}; {len(data)} binary bytes",flush=True)
            try:
                record_stage(records,key,'attempt')
                call(m['ip'],'movies/new',a.timeout,t,meta)
                record_stage(records,key,'created')
                call(m['ip'],'movies/full',a.timeout,t,data)
                record_stage(records,key,'binary-ok')
            except (OSError, ValueError, RuntimeError) as exc:
                try:
                    observed,_=inspect(m,a.timeout)
                    entries=[x for x in observed['catalogue']['movies'] if str(x.get('unique_id','')).lower()==meta['unique_id'].lower() or x.get('name')==meta['name']]
                    print(f"  Failed upload catalogue readback for {m['name']}: {entries!r} (metadata only)",flush=True)
                except Exception as read_error:
                    print(f'  Failed upload catalogue readback unavailable: {read_error}',flush=True)
                raise RuntimeError(f"{m['name']}: upload failed; no automatic retry: {exc}") from exc
            after,_=inspect(m,a.timeout);old=before['catalogue']['movies'];new=after['catalogue']['movies']
            def matches(n):return all((str(n.get(k,'')).lower()==v.lower() if k=='unique_id' else n.get(k)==v) for k,v in meta.items())
            if after['mode']!='off' or len(new)!=len(old)+1 or not all(x in new for x in old) or sum(matches(x) for x in new)!=1:raise ValueError(f"{m['name']}: catalogue readback failed")
            record_stage(records,key,'verified')
            snapshots[k]=after;print('  verified',m['name'],flush=True)
    for m in members:
        state,_=inspect(m,a.timeout)
        if state != snapshots[m['mac']] or any(not matching_entry(state['catalogue'],jobs[movie['slug'],m['mac']][0]) for movie in movies):
            raise ValueError(f"{m['name']}: final catalogue verification failed")
    print('All movie catalogues verified. Reload Area Manager. Actual playback sync still requires rehearsal.')
if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,RuntimeError) as e:
        print('STOP:',e,'\nIf upload began, partial entries may exist. Do not blindly rerun or delete all movies.',file=sys.stderr);sys.exit(2)
