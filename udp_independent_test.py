"""Independent Twinkly UDP sequence with black-frame priming and cleanup.
Requires udp_playback_test.py, udp_isolation_test.py and twinkly_inspector.py.
No cloud, group or layout writes. Use only with other clients isolated.
"""
import argparse
import socket
import time
from udp_playback_test import discover, checked, GROUP, MODE
from udp_isolation_test import mode, encode


def frame(m, rgb):
    return bytes((0,)+rgb if m['width']==4 else rgb)*m['n']


def run(a):
    members=discover(a)
    master=members[0]
    # Master first for setup; slave modes then override group-wide activation.
    black=[frame(m,(0,0,0)) for m in members]
    red=[frame(m,(round(255*a.brightness/100),0,0)) for m in members]
    for m in members:
        print(f"{m['name']}: {m['n']} LEDs, offset {m['offset']}, {m['role']}")
    print(f"Sequence: {a.dwell}s per set, {a.fps} FPS, {a.brightness}% red")
    if not a.execute:
        print('Preview: authentication only, no mode or UDP writes.')
        return 0
    tokens={}; cleanup_ok=True; frames=0; datagrams=0
    def send(sock,i,data):
        nonlocal datagrams
        m=members[i]
        for j,p in enumerate(encode(tokens[m['ip']],data,900)):
            if j and a.spacing_ms: time.sleep(a.spacing_ms/1000)
            sock.sendto(p,(m['ip'],7777)); datagrams+=1
    def stage(sock,active,duration):
        nonlocal frames
        start=time.monotonic(); end=start+duration; tick=start
        while time.monotonic()<end:
            for i,m in enumerate(members):
                send(sock,i,red[i] if i==active else black[i])
            frames+=1
            tick=max(tick+1/a.fps,time.monotonic())
            time.sleep(max(0,min(end,tick)-time.monotonic()))
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        try:
            mode(master,'off',a)
            # Clear retained slave frames while master remains off.
            for i in range(1,len(members)):
                m=members[i]; tokens[m['ip']]=mode(m,'rt',a)
                for _ in range(5):
                    send(sock,i,black[i]); time.sleep(.05)
                mode(m,'off',a)
            for i,m in enumerate(members):
                tokens[m['ip']]=mode(m,'rt',a)
                group=checked(m['ip'],GROUP,a.timeout,tokens[m['ip']])['group']
                if (group.get('uid')!=a.uid or group.get('mode')!=m['role'] or group.get('size')!=a.expected_leds or group.get('offset',0 if i==0 else None)!=m['offset']):
                    raise RuntimeError(m['name']+': group changed')
                send(sock,i,black[i])
            print('Priming all controllers with black for 2 seconds',flush=True)
            stage(sock,None,2)
            for i,m in enumerate(members):
                print(f"START {i+1}/{len(members)}: {m['name']} ({m['ip']})",flush=True)
                stage(sock,i,a.dwell)
            print(f'Sequence complete: {frames} group frames, {datagrams} datagrams; visual result not verified.')
        finally:
            # Fresh authentication for clearing, because tokens can be invalidated.
            for i in range(len(members)):
                m=members[i]
                try:
                    tokens[m['ip']]=mode(m,'rt',a)
                    for _ in range(5):
                        send(sock,i,black[i]); time.sleep(.05)
                except Exception as exc:
                    cleanup_ok=False; print(f"WARNING {m['name']}: black cleanup failed: {exc}")
            for m in reversed(members):
                try: mode(m,'off',a)
                except Exception as exc:
                    cleanup_ok=False; print(f"WARNING {m['name']}: off failed: {exc}")
    return 0 if cleanup_ok else 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='devices.json')
    p.add_argument('--uid',default='6A317CC0-F29B-42FD-885C-27C27DCC83F4')
    p.add_argument('--expected-leds',type=int,default=4900)
    p.add_argument('--expected-members',type=int,default=15)
    p.add_argument('--fps',type=float,default=5)
    p.add_argument('--spacing-ms',type=float,default=2)
    p.add_argument('--brightness',type=float,default=100)
    p.add_argument('--dwell',type=float,default=4)
    p.add_argument('--timeout',type=float,default=5)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    if not (1<=a.fps<=20 and 0<=a.spacing_ms<=10 and 0<=a.brightness<=100 and 1<=a.dwell<=10 and 0<a.timeout<=20 and 1<=a.expected_members<=30 and a.dwell*a.expected_members<=180):
        p.error('Out of test bounds')
    try: return run(a)
    except (Exception,KeyboardInterrupt) as exc:
        print('STOP:',exc); return 2

if __name__=='__main__':
    raise SystemExit(main())
