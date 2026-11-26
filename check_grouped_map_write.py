#!/usr/bin/env python3
"""Read-only diagnostic for one attempted 2D map write. Uses paced V3 transport.
No map, group, mode, movie or cloud mutations; authentication only uses POST.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sys
import update_grouped_2d_map_v3 as v3


def compare(live, expected):
    points, ref = live.get('coordinates'), expected.get('coordinates')
    if not isinstance(points, list) or len(points) != len(ref):
        return None
    out = {a: max(abs(float(p[a])-float(q[a])) for p,q in zip(points,ref)) for a in 'xyz'}
    if not all(math.isfinite(x) for x in out.values()):
        raise ValueError('Nonfinite coordinate')
    return out


def matches(live, expected, differences):
    return (differences is not None and max(differences.values()) <= v3.TOL
            and live.get('source') == expected.get('source')
            and live.get('synthesized') == expected.get('synthesized'))


def classify(live, old, target):
    d0, d1 = compare(live, old), compare(live, target)
    print('Live source:', live.get('source'), 'synthesized:', live.get('synthesized'), 'LED count:',len(live.get('coordinates',[])))
    print('Original source:', old.get('source'), 'synthesized:',old.get('synthesized'))
    print('Target source:', target.get('source'), 'synthesized:',target.get('synthesized'))
    print('Live layout UUID:', live.get('uuid'))
    print('Original layout UUID:', old.get('uuid'))
    print('Target layout UUID:', target.get('uuid'))
    for label, d in [('ORIGINAL',d0),('CORRECTED',d1)]:
        print(label, 'max coordinate differences:', 'LED count differs' if d is None else ' '.join(f'{a}={x:.9f}' for a,x in d.items()))
    a,b=matches(live,old,d0),matches(live,target,d1)
    status = 'BOTH' if a and b else 'ORIGINAL' if a else 'CORRECTED' if b else 'NEITHER'
    print('RESULT:',status)
    return status


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',default='Front_2D_corrected_v3.zip')
    p.add_argument('--member',default='BackR Bush Top')
    p.add_argument('--delay',type=float,default=3)
    p.add_argument('--timeout',type=float,default=60)
    a=p.parse_args()
    if not 0<a.timeout<=120 or not 0<=a.delay<=30: p.error('Invalid delay or timeout')
    _, oldlan, _, targetlan=v3.read_bundle(a.bundle)
    old=[m for m in oldlan['members'] if m['name']==a.member]
    target=[m for m in targetlan['members'] if m['name']==a.member]
    v3.require(len(old)==len(target)==1,'Expected exactly one named member')
    m,t=old[0],target[0]
    v3.require(v3.member_identity(m)==v3.member_identity(t),'Bundle identity differs')
    transport=v3.PacedHTTP(a.delay,a.timeout,lambda msg:print(msg,flush=True))
    identity=transport.get(m,'/xled/v1/gestalt',False)
    v3.require(v3.mac(identity['mac'])==v3.mac(m['mac']) and identity['number_of_led']==m['count'],'Wrong controller identity')
    group=transport.get(m,'/xled/v1/application/group')['group']
    v3.require(v3.role({'role':group})==v3.role(m),'Group role or offset changed: stop')
    print('Identity and group role match.')
    layout=transport.get(m,'/xled/v1/led/layout/full')
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=Path('cloud-preflight-backups')/('map-diagnostic-'+stamp)
    out.mkdir(parents=True,exist_ok=False)
    v3.save(out/'observed-layout-private.json',layout)
    print('Private captured layout:',out/'observed-layout-private.json')
    classify(layout,m['layout'],t['layout'])
    print('READ ONLY: no mapping, group, movie, mode or cloud writes.')
    return 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as e:
        print('STOP:',type(e).__name__,str(e),file=sys.stderr);sys.exit(2)
