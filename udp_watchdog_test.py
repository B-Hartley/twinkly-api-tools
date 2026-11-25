"""Single controller UDP watchdog diagnostic. Run beside existing UDP test scripts."""
import argparse
import socket
import time
from udp_playback_test import discover, checked, MODE, GROUP
from udp_isolation_test import mode, encode, login


def run(a):
    members = discover(a)
    master = members[0]
    selected = [m for m in members if a.target == m['ip'] or a.target.casefold() == m['name'].casefold()]
    if len(selected) != 1:
        raise ValueError('Target must identify exactly one member')
    m = selected[0]
    ip = m['ip']
    print(f"Target {m['name']} {ip}: {m['n']} LEDs, {m['width']} bytes/LED; reassert={a.reassert_seconds}s", flush=True)
    if not a.execute:
        print('Preview: no mode changes or UDP sends; authentication performed.')
        return 0
    level = round(255*a.brightness/100)
    rgb = {'red':(level,0,0),'green':(0,level,0),'blue':(0,0,level)}[a.color]
    frame = bytes((0,)+rgb if m['width']==4 else rgb)*m['n']
    black = bytes(len(frame))
    start = None
    count = datagrams = 0
    failed = False
    token = None
    def log(message):
        t = time.monotonic()-start if start is not None else 0
        print(f'[{t:7.2f}s] {message}', flush=True)
    def send(sock, packets):
        for i,p in enumerate(packets):
            if i and a.spacing_ms: time.sleep(a.spacing_ms/1000)
            sock.sendto(p,(ip,7777))
    try:
        mode(master,'off',a)
        token = mode(m,'rt',a)
        group = checked(ip,GROUP,a.timeout,token)['group']
        if (group.get('uid')!=a.uid or group.get('mode')!=m['role'] or group.get('size')!=a.expected_leds or group.get('offset',0 if m is master else None)!=m['offset']):
            raise RuntimeError('Group changed; aborting')
        packets = encode(token,frame,900)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            start=time.monotonic(); end=start+a.seconds
            next_frame=start; next_poll=start+2; next_report=start+5
            next_assert=start+a.reassert_seconds if a.reassert_seconds else float('inf')
            previous = 'rt'
            log(f'START {a.color}; {len(packets)} packets/frame; note elapsed time when light changes')
            while time.monotonic()<end:
                now=time.monotonic()
                if now>=next_poll:
                    try:
                        actual=checked(ip,MODE,a.timeout,token).get('mode')
                        log(f'MODE={actual}'+(' CHANGED' if actual!=previous else ''))
                        previous=actual
                    except Exception as exc:
                        log(f'MODE READ ERROR: {exc}')
                        # Observation run deliberately does not replace the token.
                    next_poll=time.monotonic()+2
                if time.monotonic()>=end: break
                if time.monotonic()>=next_assert:
                    try:
                        checked(ip,MODE,a.timeout,token,{'mode':'rt'})
                        actual=checked(ip,MODE,a.timeout,token).get('mode')
                        if actual!='rt': raise RuntimeError(f'Mode is {actual}')
                        log('REASSERT rt verified with existing token')
                    except Exception as exc:
                        log(f'REASSERT ERROR: {exc}; refreshing token and mode')
                        token=mode(m,'rt',a)
                        packets=encode(token,frame,900)
                        log('New UDP token applied')
                    next_assert=time.monotonic()+a.reassert_seconds
                if time.monotonic()>=end: break
                send(sock,packets); count+=1; datagrams+=len(packets)
                if time.monotonic()>=next_report:
                    log(f'{count} frames / {datagrams} datagrams submitted')
                    next_report=time.monotonic()+5
                next_frame=max(next_frame+1/a.fps,time.monotonic())
                time.sleep(max(0,min(end,next_frame)-time.monotonic()))
            log(f'END {count} frames / {datagrams} datagrams; visual result not verified')
    finally:
        try:
            token=mode(m,'rt',a)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                for _ in range(5):
                    send(sock,encode(token,black,900)); time.sleep(.1)
            log('Black cleanup frames submitted')
        except Exception as exc:
            failed=True; log(f'BLACK CLEANUP FAILED: {exc}')
        for device in ([m,master] if m is not master else [master]):
            try: mode(device,'off',a)
            except Exception as exc:
                failed=True; log(f'OFF FAILED {device["name"]}: {exc}; check manually')
    return 2 if failed else 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='devices.json')
    p.add_argument('--uid',default='6A317CC0-F29B-42FD-885C-27C27DCC83F4')
    p.add_argument('--expected-leds',type=int,default=4900)
    p.add_argument('--expected-members',type=int,default=15)
    p.add_argument('--target',default='192.168.21.145')
    p.add_argument('--color',choices=['red','green','blue'],default='green')
    p.add_argument('--brightness',type=float,default=100)
    p.add_argument('--seconds',type=float,default=60)
    p.add_argument('--fps',type=float,default=5)
    p.add_argument('--spacing-ms',type=float,default=2)
    p.add_argument('--timeout',type=float,default=2)
    p.add_argument('--reassert-seconds',type=float,default=0)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if not (0<=a.brightness<=100 and 1<=a.seconds<=60 and 1<=a.fps<=25 and 0<=a.spacing_ms<=20 and 0<a.timeout<=10 and (a.reassert_seconds==0 or 2<=a.reassert_seconds<=30)):
        p.error('Out of test bounds')
    try: return run(a)
    except (Exception,KeyboardInterrupt) as exc:
        print('STOP:',exc); return 2

if __name__=='__main__':
    raise SystemExit(main())
