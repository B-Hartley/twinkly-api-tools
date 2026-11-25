"""Twinkly single-target UDP diagnostics. Requires existing udp_playback_test.py
and twinkly_inspector.py in the repository. No group, map or cloud writes.
"""
import argparse
import base64
import socket
import time
from udp_playback_test import discover, checked, MODE, GROUP
from twinkly_inspector import authenticate


def login(m, a):
    token, _ = authenticate(m['ip'], a.timeout)
    if not token:
        raise RuntimeError(m['name'] + ': authentication failed')
    return token


def mode(m, desired, a):
    for attempt in range(2):
        token = login(m, a)
        try:
            checked(m['ip'], MODE, a.timeout, token, {'mode': desired})
            actual = checked(m['ip'], MODE, a.timeout, token).get('mode')
            if actual != desired:
                raise RuntimeError(f"{m['name']}: requested {desired}, got {actual}")
            print(f"{m['name']}: {desired} verified", flush=True)
            return token
        except RuntimeError as exc:
            if 'HTTP 401' not in str(exc) or attempt:
                raise
            print(f"{m['name']}: HTTP 401, reauthenticating once", flush=True)


def encode(token, frame, chunk):
    key = base64.b64decode(token, validate=True)
    if len(key) != 8 or not frame or (len(frame)+chunk-1)//chunk > 256:
        raise ValueError('Invalid token or frame size')
    return [b'\x03'+key+b'\x00\x00'+bytes([i//chunk])+frame[i:i+chunk]
            for i in range(0, len(frame), chunk)]


def run(a):
    members = discover(a)
    master = members[0]
    selected = [m for m in members if m['ip'] == a.target or m['name'].casefold() == a.target.casefold()]
    if len(selected) != 1:
        raise ValueError('Target must identify exactly one controller')
    m = selected[0]
    if a.chunk_bytes % m['width']:
        raise ValueError('Chunk size must be a multiple of pixel width')
    print(f"Target: {m['name']} {m['ip']} | {m['role']} | {m['n']} LEDs | {m['width']} bytes/LED")
    print(f"UDP destination ONLY: {m['ip']}:7777; chunk={a.chunk_bytes}; fps={a.fps}; mode strategy={a.mode_strategy}")
    if not a.execute:
        print('Preview only; no mode changes or UDP frames. Authentication performed.')
        return 0
    touched = []
    cleanup_failed = False
    try:
        # Reset the master before each test to avoid leaving a previous stream active.
        touched.append(master)
        mode(master, 'off', a)
        if a.mode_strategy == 'master' or m is master:
            master_token = mode(master, 'rt', a)
        if m is master:
            token = master_token
        else:
            touched.append(m)
            if a.mode_strategy == 'target':
                token = mode(m, 'rt', a)
            else:
                token = login(m, a)
        # Recheck the selected controller's group after mode preparation.
        group = checked(m['ip'], GROUP, a.timeout, token)['group']
        if (group.get('uid') != a.uid or group.get('mode') != m['role'] or
            group.get('size') != a.expected_leds or group.get('offset', 0 if m is master else None) != m['offset']):
            raise RuntimeError('Group changed; aborting')
        level = round(255*a.brightness/100)
        rgb = {'red':(level,0,0), 'green':(0,level,0), 'blue':(0,0,level), 'black':(0,0,0)}[a.color]
        pixel = bytes((0,)+rgb if m['width']==4 else rgb)
        frame = pixel*m['n']
        packets = encode(token, frame, a.chunk_bytes)
        print(f"START {m['name']}: {a.color}, {a.brightness}% | {len(frame)} LED bytes | {len(packets)} datagrams/frame", flush=True)
        count=0
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            start=time.monotonic(); end=start+a.seconds; next_frame=start
            while time.monotonic()<end:
                for i,pkt in enumerate(packets):
                    if i and a.spacing_ms: time.sleep(a.spacing_ms/1000)
                    sock.sendto(pkt,(m['ip'],7777))
                count+=1
                next_frame=max(next_frame+1/a.fps,time.monotonic())
                time.sleep(max(0,min(end,next_frame)-time.monotonic()))
        print(f"END: {count} frames, {count*len(packets)} UDP datagrams; visual result not verified.")
    finally:
        # Master last, so it cannot be left in realtime by slave cleanup.
        for device in reversed(touched):
            try: mode(device,'off',a)
            except Exception as exc:
                cleanup_failed=True
                print(f"WARNING: {device['name']} off not verified: {exc}; check manually")
    return 2 if cleanup_failed else 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='devices.json')
    p.add_argument('--uid',default='6A317CC0-F29B-42FD-885C-27C27DCC83F4')
    p.add_argument('--expected-leds',type=int,default=4900)
    p.add_argument('--expected-members',type=int,default=15)
    p.add_argument('--target',required=True)
    p.add_argument('--mode-strategy',choices=['target','master'],default='target')
    p.add_argument('--chunk-bytes',type=int,choices=[300,600,900],default=900)
    p.add_argument('--fps',type=float,default=5)
    p.add_argument('--spacing-ms',type=float,default=2)
    p.add_argument('--brightness',type=float,default=100)
    p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--timeout',type=float,default=5)
    p.add_argument('--color',choices=['red','green','blue','black'],default='green')
    p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if not (1<=a.fps<=25 and 0<=a.spacing_ms<=20 and 0<=a.brightness<=100 and 1<=a.seconds<=60 and 0<a.timeout<=20):
        p.error('Out of test bounds')
    try: return run(a)
    except (Exception,KeyboardInterrupt) as exc:
        print('STOP:',exc); return 2

if __name__=='__main__':
    raise SystemExit(main())
