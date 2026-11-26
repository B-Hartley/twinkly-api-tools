#!/usr/bin/env python3
"""Read-only catalogue audit: no movie, mode, group or map writes."""
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
import upload_movies as u


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--timeout', type=float, default=45)
    p.add_argument('--gap', type=float, default=2)
    a = p.parse_args()
    if a.timeout <= 0 or a.gap < 0:
        p.error('Invalid timeout or gap')
    members, movies, jobs = u.read_bundle(None)
    original = u.call
    def paced(ip, endpoint, timeout, token=None, payload=None):
        if payload is not None and endpoint not in ('login', 'verify'):
            raise RuntimeError('Read-only guard')
        time.sleep(a.gap)
        return original(ip, endpoint, timeout, token, payload)
    u.call = paced
    report = {'controllers': [], 'movies': {m['slug']: [] for m in movies}}
    path = Path(__file__).resolve().parent / ('private-movie-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    for m in members:
        row = {'name': m['name'], 'mac': m['mac']}
        try:
            state, _ = u.inspect(m, a.timeout)
            row.update(state)
            cat = state['catalogue']
            print(f"{m['name']}: mode={state['mode']}, free={cat['available_frames']}, entries={len(cat['movies'])}", flush=True)
            for entry in cat['movies']:
                print('  ' + json.dumps({k:entry.get(k) for k in ('id','name','unique_id','frames_number','leds_per_frame','fps','descriptor_type')}, ensure_ascii=True), flush=True)
            for movie in movies:
                meta, _ = jobs[movie['slug'], m['mac']]
                found = [e for e in cat['movies'] if e.get('name') == movie['name'] or str(e.get('unique_id','')).lower() == movie['unique_id'].lower()]
                exact = len(found) == 1 and all(str(found[0].get(k,'')).lower() == v.lower() if k == 'unique_id' else found[0].get(k) == v for k,v in meta.items())
                report['movies'][movie['slug']].append('MATCH' if exact else 'DIFFERENT' if found else 'ABSENT')
        except Exception as e:
            row['error'] = f'{type(e).__name__}: {e}'
            print(f"{m['name']}: ERROR {row['error']}", flush=True)
            for movie in movies: report['movies'][movie['slug']].append('UNKNOWN')
        report['controllers'].append(row)
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('\nSummary (catalogue metadata only, not binary playback):')
    for slug, states in report['movies'].items():
        print(slug + ': ' + ', '.join(f'{s}={states.count(s)}' for s in ('MATCH','ABSENT','DIFFERENT','UNKNOWN')))
    print('Saved private evidence:', path.name)
    print('READ ONLY: no movie, map, group or mode writes.')

if __name__ == '__main__':
    main()
