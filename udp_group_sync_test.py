#!/usr/bin/env python3
"""Concurrent group realtime diagnostic. No group, mapping or cloud writes.
Requires udp_playback_test.py and twinkly_inspector.py from the repository.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
import time
from udp_playback_test import discover, checked, packets, MODE, GROUP
from twinkly_inspector import authenticate


def parallel(pool, members, fn):
    futures = {pool.submit(fn, m): m for m in members}
    results, errors = {}, []
    for f in as_completed(futures):
        m = futures[f]
        try:
            results[m['ip']] = f.result()
        except Exception as exc:
            errors.append(f"{m['name']}: {exc}")
    return results, errors


def login(m, a):
    token, _ = authenticate(m['ip'], a.timeout)
    if not token:
        raise RuntimeError('authentication failed')
    return token


def set_mode(m, token, desired, a):
    for attempt in range(2):
        try:
            checked(m['ip'], MODE, a.timeout, token, {'mode': desired})
            return token
        except RuntimeError as exc:
            if attempt or 'HTTP 401' not in str(exc):
                raise
            token = login(m, a)


def run(a):
    members = discover(a)
    print(f"Verified {len(members)} controllers / {sum(m['n'] for m in members)} LEDs")
    for m in members:
        print(f"{m['name']:22} {m['ip']:16} {m['n']:4} LEDs {m['role']} offset={m['offset']}")
    if not a.execute:
        print('Preview: authentication only, no mode changes or UDP frames.')
        return 0
    level = round(255*a.brightness/100)
    rgb = {'red':(level,0,0), 'green':(0,level,0), 'blue':(0,0,level)}[a.color]
    black = [bytes(m['n']*m['width']) for m in members]
    lit = [bytes((0,)+rgb if m['width']==4 else rgb)*m['n'] for m in members]
    tokens = {}
    count = datagrams = 0
    cleanup_errors = []
    started = time.monotonic()
    def log(s):
        print(f'[{time.monotonic()-started:7.2f}s] {s}', flush=True)
    with ThreadPoolExecutor(max_workers=len(members)) as pool, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        def send(active):
            nonlocal count, datagrams
            for i,m in enumerate(members):
                token = tokens.get(m['ip'])
                if not token:
                    continue
                data = lit[i] if active == -2 or active == i else black[i]
                for p in packets(token,data):
                    sock.sendto(p,(m['ip'],7777)); datagrams += 1
            count += 1
        def stream(duration, active):
            start=time.monotonic(); end=start+duration; tick=start
            while time.monotonic()<end:
                send(active)
                tick=max(tick+1/a.fps,time.monotonic())
                time.sleep(max(0,min(end,tick)-time.monotonic()))
        def modes(desired):
            got, errors=parallel(pool,members,lambda m:set_mode(m,tokens[m['ip']],desired,a))
            tokens.update(got)
            return errors
        def verify_modes():
            got,errors=parallel(pool,members,lambda m:checked(m['ip'],MODE,a.timeout,tokens[m['ip']]).get('mode'))
            log('MODE '+', '.join(f"{m['name']}={got.get(m['ip'],'ERROR')}" for m in members))
            for error in errors: log(error)
            return got,errors
        try:
            fresh,errors=parallel(pool,members,lambda m:login(m,a)); tokens.update(fresh)
            if errors: raise RuntimeError('; '.join(errors))
            log('Concurrent authentication complete')
            # Activate and prime in parallel: avoid leaving master off during playback.
            futures={pool.submit(set_mode,m,tokens[m['ip']],'rt',a):m for m in members}
            activation_errors=[]
            for f in as_completed(futures):
                m=futures[f]
                try:
                    tokens[m['ip']]=f.result()
                    i=members.index(m)
                    for p in packets(tokens[m['ip']],black[i]):
                        sock.sendto(p,(m['ip'],7777)); datagrams+=1
                except Exception as exc: activation_errors.append(f"{m['name']}: {exc}")
            if activation_errors: raise RuntimeError('; '.join(activation_errors))
            log('All realtime commands accepted; priming black frames')
            stream(2,-1)
            # Mode reads run separately so frame sending does not stop for HTTP.
            with ThreadPoolExecutor(max_workers=1) as monitor:
                pending=None; next_check=time.monotonic()
                start=time.monotonic(); deadline=start+(a.dwell*len(members) if a.pattern=='sequence' else a.seconds)
                tick=start; previous=None; next_report=start+5
                while time.monotonic()<deadline:
                    now=time.monotonic()
                    active=min(len(members)-1,int((now-start)/a.dwell)) if a.pattern=='sequence' else -2
                    if active!=previous:
                        log('START '+(members[active]['name'] if active>=0 else 'ALL CONTROLLERS')); previous=active
                    send(active)
                    if pending and pending.done():
                        pending.result(); pending=None
                    if a.monitor_seconds and pending is None and now>=next_check:
                        pending=monitor.submit(verify_modes); next_check=now+a.monitor_seconds
                    if now>=next_report:
                        log(f'{count} frames / {datagrams} datagrams submitted'); next_report=now+5
                    tick=max(tick+1/a.fps,time.monotonic())
                    time.sleep(max(0,min(deadline,tick)-time.monotonic()))
                log('Playback complete; clearing frames')
                stream(2,-1)
                if pending: pending.result()
        finally:
            # Refresh tokens before cleanup, re-enter realtime together to clear retained buffers.
            fresh,errors=parallel(pool,members,lambda m:login(m,a)); tokens.update(fresh)
            cleanup_errors.extend(errors)
            available=[m for m in members if m['ip'] in tokens]
            got,errors=parallel(pool,available,lambda m:set_mode(m,tokens[m['ip']],'rt',a)); tokens.update(got)
            cleanup_errors.extend(errors)
            try: stream(2,-1)
            except Exception as exc: cleanup_errors.append('Black cleanup: '+str(exc))
            got,errors=parallel(pool,available,lambda m:set_mode(m,tokens[m['ip']],'off',a)); tokens.update(got)
            cleanup_errors.extend(errors)
            got,errors=verify_modes(); cleanup_errors.extend(errors)
            cleanup_errors.extend(m['name']+': OFF not verified' for m in members if got.get(m['ip'])!='off')
            for err in cleanup_errors: log('WARNING '+err)
            if not cleanup_errors: log('All controllers OFF verified')
    log(f'Total {count} group frames / {datagrams} UDP datagrams; visual result not verified')
    return 2 if cleanup_errors else 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='devices.json')
    p.add_argument('--uid',default='6A317CC0-F29B-42FD-885C-27C27DCC83F4')
    p.add_argument('--expected-leds',type=int,default=4900)
    p.add_argument('--expected-members',type=int,default=15)
    p.add_argument('--fps',type=float,default=15)
    p.add_argument('--brightness',type=float,default=100)
    p.add_argument('--dwell',type=float,default=4)
    p.add_argument('--seconds',type=float,default=60)
    p.add_argument('--pattern',choices=['sequence','solid'],default='sequence')
    p.add_argument('--color',choices=['red','green','blue'],default='red')
    p.add_argument('--monitor-seconds',type=float,default=5)
    p.add_argument('--timeout',type=float,default=5)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if not (1<=a.fps<=25 and 0<=a.brightness<=100 and 1<=a.dwell<=10 and 1<=a.seconds<=120 and 0<a.timeout<=15 and 1<=a.expected_members<=30 and 0<=a.monitor_seconds<=30 and a.dwell*a.expected_members<=180):
        p.error('Out of test bounds')
    try: return run(a)
    except (Exception,KeyboardInterrupt) as exc:
        print('STOP:',exc); return 2

if __name__=='__main__':
    raise SystemExit(main())
