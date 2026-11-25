"""Bounded, configurable-brightness Twinkly group UDP test. Python 3.10+, stdlib only.
Run from twinkly-api-tools. Experimental v3 protocol, no group/map writes.
"""
import argparse
import base64
import socket
import time
from types import SimpleNamespace
from twinkly_inspector import authenticate, get_devices, request

GROUP='/xled/v1/application/group'
MODE='/xled/v1/led/mode'
GESTALT='/xled/v1/gestalt'

def checked(ip,path,timeout,token=None,payload=None):
    r=request(ip,path,token=token,timeout=timeout,payload=payload,method='GET' if payload is None else 'POST')
    if not r.get('ok'):
        raise RuntimeError(f'{ip} {path}: {r.get("error", "device rejected request")}')
    return r['data']

def packets(token,frame):
    key=base64.b64decode(token,validate=True)
    if len(key)!=8 or not frame or len(frame)>900*256:
        raise ValueError('Invalid token or frame length')
    return [b'\x03'+key+b'\x00\x00'+bytes([i//900])+frame[i:i+900] for i in range(0,len(frame),900)]

def discover(a):
    members=[]
    for d in get_devices(SimpleNamespace(config=a.config,ips=None)):
        ip=d['ip']; g=checked(ip,GESTALT,a.timeout)
        token,_=authenticate(ip,a.timeout)
        if not token: raise RuntimeError(f'{d["name"]}: authentication failed')
        role=checked(ip,GROUP,a.timeout,token)['group']
        if str(role.get('uid','')).lower()!=a.uid.lower(): continue
        if role['uid']!=a.uid: raise ValueError(f'{d["name"]}: exact UUID mismatch; repair first')
        n=g['number_of_led']; width=g.get('bytes_per_led')
        profile=str(g.get('led_profile','')).upper()
        inferred={'RGB':3,'RGBW':4,'WRGB':4}.get(profile)
        if width is None: width=inferred
        if type(n)!=int or n<=0 or width not in (3,4) or (inferred is not None and width!=inferred): raise ValueError('Invalid LED format')
        mode=role['mode']; offset=role.get('offset',0 if mode=='master' else None)
        members.append(dict(d,token=token,n=n,width=width,role=mode,offset=offset,size=role['size'],mac=g['mac']))
    if not members or sum(m['role']=='master' for m in members)!=1 or len({m['mac'] for m in members})!=len(members): raise ValueError('Group identity/master invalid')
    if any(type(m['offset'])!=int for m in members): raise ValueError('Missing offset')
    members.sort(key=lambda m:m['offset']); offset=0
    for i,m in enumerate(members):
        if m['offset']!=offset or m['role']!=('master' if i==0 else 'slave'): raise ValueError('Invalid group offsets or roles')
        offset+=m['n']
    if offset!=a.expected_leds or len(members)!=a.expected_members or any(m['size']!=offset for m in members): raise ValueError('Incomplete group')
    return members

def render(members,a,t):
    colors={'red':(1,0,0,0),'green':(0,1,0,0),'blue':(0,0,1,0),'white':(0,0,0,1),'black':(0,0,0,0)}
    level=round(255*a.brightness/100); frames=[]
    selected=next((i for i,m in enumerate(members) if m['ip']==a.target or m['name'].casefold()==str(a.target).casefold()),None)
    for i,m in enumerate(members):
        active=a.pattern=='solid' or (a.pattern=='isolate' and i==selected) or (a.pattern=='sequence' and i==int(t/a.dwell)%len(members))
        rgbw=colors[a.color] if active else colors['black']
        r,g,b,w=[int(v*level) for v in rgbw]
        if m['width']==3 and w: r=g=b=w
        pixel=bytes((w,r,g,b) if m['width']==4 else (r,g,b))
        frames.append(pixel*m['n'])
    return frames

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='devices.json'); p.add_argument('--uid',required=True)
    p.add_argument('--expected-leds',type=int,required=True); p.add_argument('--expected-members',type=int,required=True)
    p.add_argument('--transport',choices=['udp-per-controller','udp-master-combined'],default='udp-per-controller')
    p.add_argument('--pattern',choices=['solid','isolate','sequence'],default='isolate'); p.add_argument('--target')
    p.add_argument('--color',choices=['red','green','blue','white','black'],default='red')
    p.add_argument('--brightness',type=float,default=2); p.add_argument('--fps',type=float,default=10); p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--spacing-ms',type=float,default=0); p.add_argument('--dwell',type=float,default=2); p.add_argument('--timeout',type=float,default=5)
    p.add_argument('--execute',action='store_true'); a=p.parse_args()
    if not (0<a.brightness<=100 and 1<=a.fps<=25 and 1<=a.seconds<=60 and 0<=a.spacing_ms<=20 and 0.25<=a.dwell<=10 and 0<a.timeout<=20): p.error('Out of safe test bounds')
    members=discover(a); master=members[0]
    if a.pattern=='isolate' and sum(m['ip']==a.target or m['name'].casefold()==str(a.target).casefold() for m in members)!=1: p.error('Supply exact --target name or IP')
    if a.transport=='udp-master-combined' and len({m['width'] for m in members})!=1: p.error('Combined frame requires uniform pixel width')
    for m in members: print(f'{m["name"]:22} {m["ip"]:16} {m["n"]:4} LEDs offset={m["offset"]:4} {m["role"]}')
    print(f'Test: {a.transport}, {a.pattern}, {a.color}, {a.brightness}% at {a.fps} fps')
    if not a.execute: print('Preview only: no mode or UDP writes. Authentication performed.'); return 0
    frames_sent=datagrams=bytes_sent=0; attempted=False; cleanup_ok=True
    try:
        attempted=True
        checked(master['ip'],MODE,a.timeout,master['token'],{'mode':'rt'})
        if checked(master['ip'],MODE,a.timeout,master['token']).get('mode')!='rt': raise RuntimeError('Master realtime mode not confirmed')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            start=time.monotonic(); deadline=start+a.seconds
            while time.monotonic()<deadline:
                t=time.monotonic()-start; frames=render(members,a,t)
                targets=[(master,b''.join(frames))] if a.transport=='udp-master-combined' else zip(members,frames)
                for m,frame in targets:
                    for j,pkt in enumerate(packets(m['token'],frame)):
                        if j and a.spacing_ms: time.sleep(a.spacing_ms/1000)
                        bytes_sent+=sock.sendto(pkt,(m['ip'],7777)); datagrams+=1
                frames_sent+=1
                time.sleep(max(0,min(deadline,start+frames_sent/a.fps)-time.monotonic()))
            elapsed=time.monotonic()-start
            print(f'Submitted {frames_sent} frames, {datagrams} UDP datagrams, {bytes_sent} bytes, {frames_sent/elapsed:.2f} fps. Visual playback NOT verified.')
    finally:
        if attempted:
            try:
                token,_=authenticate(master['ip'],a.timeout)
                if not token: raise RuntimeError('cleanup authentication failed')
                checked(master['ip'],MODE,a.timeout,token,{'mode':'off'})
                if checked(master['ip'],MODE,a.timeout,token).get('mode')!='off': raise RuntimeError('off not confirmed')
                print('Master OFF verified; visually check all slaves.')
            except Exception as e:
                cleanup_ok=False; print('WARNING: OFF failed:',e,'; switch lights off manually.')
    return 0 if cleanup_ok else 2

if __name__=='__main__':
    try: raise SystemExit(main())
    except (Exception,KeyboardInterrupt) as e:
        print('STOP:',e); raise SystemExit(2)
