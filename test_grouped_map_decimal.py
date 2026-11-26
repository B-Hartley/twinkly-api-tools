#!/usr/bin/env python3
"""Single-controller guarded mapping test. Requires V4 beside this script."""
import argparse, copy, datetime as dt, json, math, sys
from pathlib import Path
import update_grouped_2d_map_v4 as v4

# Six fixed decimal places, including tiny values. No exponent notation.
def fixed6(value):
    if type(value) is float:
        v4.require(math.isfinite(value), 'Nonfinite number')
        return format(value, '.6f')
    if isinstance(value, dict):
        return '{'+','.join(json.dumps(k)+':'+fixed6(v) for k,v in value.items())+'}'
    if isinstance(value, list):
        return '['+','.join(fixed6(v) for v in value)+']'
    return json.dumps(value, separators=(',',':'), allow_nan=False)

class DiagnosticHTTP(v4.PacedHTTP):
    def __init__(self, *args, directory, **kwargs):
        super().__init__(*args, **kwargs)
        self.directory=directory
    def write_layout(self, m, payload):
        token=self.auth(m)
        body=fixed6(payload).encode('ascii')
        (self.directory/'payload-private.json').write_bytes(body)
        def fetch():
            req=v4.urllib.request.Request('http://'+m['ip']+'/xled/v1/led/layout/full',data=body,method='POST',headers={'Accept':'application/json','Connection':'close','Content-Type':'application/json','X-Auth-Token':token})
            with self.opener(req,timeout=self.timeout) as response:
                raw=response.read(65537)
                (self.directory/'write-response-private.txt').write_bytes(raw)
                self.log(f'HTTP {response.status}; response bytes={len(raw)}; payload bytes={len(body)}')
                v4.require(len(raw)<=65536,'Response too large')
                if response.headers.get('Content-Length') is not None and response.headers.get('Transfer-Encoding') is None:
                    v4.require(len(raw)==int(response.headers['Content-Length']),'Incomplete write reply')
                result=json.loads(raw)
                self.log('Layout API code: '+str(result.get('code')))
                if result.get('code')!=1000:
                    self.log('Rejection response saved privately: '+str(self.directory/'write-response-private.txt'))
                    raise ValueError('Controller rejected write; inspect saved response')
                return result
        return self.external('POST layout '+m['name'],fetch)

def check(transport,m):
    g=transport.get(m,'/xled/v1/gestalt',False)
    r=transport.get(m,'/xled/v1/application/group')['group']
    v4.require(v4.mac(g['mac'])==v4.mac(m['mac']) and g['number_of_led']==m['count'] and v4.role({'role':r})==v4.role(m),'Identity or group mismatch')
    v4.require(transport.get(m,'/xled/v1/led/mode')['mode']=='off','Controller must be off')
    v4.require(transport.get(m,'/xled/v1/movies')['movies']==[],'Movies must be empty')
    return transport.get(m,'/xled/v1/led/layout/full')

def matches(a,b):
    return all(a.get(k)==b.get(k) for k in ('source','synthesized','uuid')) and v4.delta(a['coordinates'],b['coordinates'])<=v4.TOL

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',default='Front_2D_corrected_v3.zip')
    p.add_argument('--recovery-layout',required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--delay',type=float,default=3)
    p.add_argument('--timeout',type=float,default=60)
    a=p.parse_args()
    v4.require(2<=a.delay<=30 and 0<a.timeout<=120,'Invalid timing')
    bundle=v4.read_bundle(a.bundle);v4.prepare(bundle)
    _,original,_,target=bundle
    m=next(x for x in original['members'] if x['name']=='BackR Bush Top')
    t=next(x for x in target['members'] if x['name']==m['name'])
    recovery=json.loads(Path(a.recovery_layout).read_text())
    expected=t['layout']
    v4.require(all(recovery.get(k)==expected.get(k) for k in ('source','synthesized','uuid')),'Recovery metadata differs')
    v4.require(len(recovery['coordinates'])==len(expected['coordinates']),'Recovery length differs')
    anomalies=[(i,k) for i,(p,q) in enumerate(zip(recovery['coordinates'],expected['coordinates'])) for k in 'xyz' if abs(p[k]-q[k])>v4.TOL]
    v4.require(anomalies==[(172,'x')],'Unexpected recovery anomaly')
    payload={k:copy.deepcopy(v) for k,v in expected.items() if k!='code'}
    wire=json.loads(fixed6(payload));v4.require(v4.delta(wire['coordinates'],expected['coordinates'])<=.00000051,'Encoding error')
    directory=Path('cloud-preflight-backups')/('decimal-test-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True,exist_ok=False)
    def log(s):
        print(s,flush=True)
        with (directory/'journal.txt').open('a',encoding='utf8') as f:f.write(s+'\n')
    transport=DiagnosticHTTP(a.delay,a.timeout,log,directory=directory)
    live=check(transport,m);v4.save(directory/'before-private.json',live)
    if matches(live,expected):
        log('Already CORRECTED; no write needed.');return 0
    v4.require(matches(live,recovery) or matches(live,m['layout']),'Live map differs from original and reviewed recovery; stop')
    log('PASS: identity, grouping, off mode, empty catalogue and known starting map. Private evidence: '+str(directory))
    if not a.execute:
        log('READ ONLY: no layout write.');return 0
    v4.require(input('Type TEST BACKR TOP to write only this controller: ').strip()=='TEST BACKR TOP','Cancelled')
    v4.require(matches(check(transport,m),live),'State changed after confirmation')
    log('ATTEMPT single LAN layout write')
    try:
        transport.write_layout(m,payload)
    except Exception:
        log('Write rejected or ambiguous; no retry. Run read-only diagnostic to inspect live state.')
        raise
    after=check(transport,m);v4.save(directory/'after-private.json',after)
    log('Maximum coordinate error: '+str(v4.delta(after['coordinates'],expected['coordinates'])))
    v4.require(matches(after,expected),'Readback mismatch; stop')
    log('PASS CORRECTED: BackR Bush Top only. No other controller or cloud map changed.')
    return 0
if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:
        print('STOP:',type(e).__name__,str(e),file=sys.stderr);sys.exit(2)
