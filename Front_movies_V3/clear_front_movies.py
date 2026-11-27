#!/usr/bin/env python3
"""Clear native movie/playlist storage on the verified Front group only.
Place beside upload_movies.py and manifest.json. Default scope is local and cloud; default operation is read-only."""
import argparse
import json
import ipaddress
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import sys
import upload_movies as u
from cloud_movie_cleanup import inspect_cloud


def delete_movies(member, token, timeout):
    ip = ipaddress.ip_address(member['ip'])
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError('Expected private LAN IPv4 address')
    req = urllib.request.Request(f'http://{ip}/xled/v1/movies', method='DELETE',
                                 headers={'X-Auth-Token': token, 'Accept': 'application/json', 'Connection': 'close'})
    u.PACING.wait(str(ip), 'movies')
    try:
        with u.opener().open(req, timeout=timeout) as response:
            raw = response.read(1024*1024+1)
    except OSError:
        u.PACING.backoff(str(ip))
        raise
    finally:
        u.PACING.finished('movies', str(ip))
    if len(raw)>1024*1024:
        raise ValueError('Oversized API reply')
    try:
        result=json.loads(raw)
    except ValueError:
        s=raw.decode().lstrip(); result,end=json.JSONDecoder().raw_decode(s)
        if s[end:].strip().lower()!='connection: close': raise
    if not isinstance(result,dict) or result.get('code')!=1000:
        raise ValueError('DELETE movies rejected')


def snapshot(members, timeout, workers=3):
    result={}
    observed,_=u.inspect_group(members,timeout,workers,u.PACING)
    for m,state,error in observed:
        if error is not None:
            raise RuntimeError(f"{m['name']}: inspection failed") from error
        if state['mode']!='off':
            raise ValueError(f"{m['name']}: controller must be OFF")
        result[m['mac']]=state
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--timeout',type=float,default=30)
    p.add_argument('--workers',type=int,choices=range(1,7),default=3)
    scope=p.add_mutually_exclusive_group()
    scope.add_argument('--local-only',action='store_true')
    scope.add_argument('--cloud-only',action='store_true')
    a=p.parse_args()
    if a.timeout <= 0: p.error('Invalid timeout')
    members,movies,_=u.read_bundle(None)
    if not a.local_only:
        inspect_cloud(members,a.timeout)
        if a.execute:
            raise ValueError('Cloud deletion blocked: compiled-effect ownership schema not yet verified. No local or cloud deletions attempted.')
    if a.cloud_only:
        return
    before=snapshot(members,a.timeout,a.workers)
    needed=sum(m['frames'] for m in movies)
    for m in members:
        cat=before[m['mac']]['catalogue']
        print(f"{m['name']:24s} movies={len(cat['movies']):2d} free={cat['available_frames']:5d} mode=off")
        for movie in cat['movies']:
            print(f"  {movie.get('id')}: {movie.get('name')} ({movie.get('frames_number')} frames)")
    print('All 15 identities, roles, offsets and group UUID verified.')
    print('DELETE removes all stored movies and playlists; original movie bytes cannot be restored from metadata.')
    if not a.execute:
        print('READ ONLY: no movies deleted. Use --local-only --execute for confirmed local deletion; cloud execution requires verified ownership.')
        return
    if input('Type CLEAR ALL FRONT MOVIES to permanently clear these catalogues: ')!='CLEAR ALL FRONT MOVIES':
        print('Cancelled; no movies deleted.'); return
    if snapshot(members,a.timeout,a.workers)!=before:
        raise ValueError('Preflight changed; no movies deleted')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=Path(__file__).resolve().parent/f'private-movie-clear-{stamp}.json'
    with path.open('x',encoding='utf-8') as f:
        json.dump({'note':'Catalogue metadata only; not a restorable movie backup','controllers':[{ 'identity':m, 'before':before[m['mac']]} for m in members]},f,indent=2)
    print('Private catalogue snapshot:',path,flush=True)
    def clear_member(m,stop):
        current,token=u.inspect(m,a.timeout)
        if current!=before[m['mac']]:
            raise ValueError(f"{m['name']}: changed since preflight")
        if stop.is_set(): return 'cancelled'
        if current['catalogue']['movies']:
            delete_movies(m,token,a.timeout)
        after,_=u.inspect(m,a.timeout)
        if after['mode']!='off' or after['catalogue']['movies']:
            raise ValueError(f"{m['name']}: empty catalogue readback failed")
        u.capacity(after['catalogue'],movies)
        print(f"{m['name']:24s} EMPTY, free={after['catalogue']['available_frames']} (need {needed})",flush=True)
        return 'done'
    u.upload_group_movie(members,clear_member,a.workers,u.PACING)
    final=snapshot(members,a.timeout,a.workers)
    for state in final.values():
        if state['catalogue']['movies']: raise ValueError('Final catalogue verification failed')
        u.capacity(state['catalogue'],movies)
    print('PASS: all 15 catalogues empty, all 5 replacement movies fit. No group or mapping writes.')

if __name__=='__main__':
    try: main()
    except (ValueError,OSError,KeyError,RuntimeError) as exc:
        print('STOP:',exc,'\nIf deletion began, some controllers may already be cleared. Inspect before retrying.',file=sys.stderr)
        sys.exit(2)
