#!/usr/bin/env python3
"""Offline Front movie compiler from V3 backup. No network operations."""
import argparse, bisect, colorsys, hashlib, json, math, uuid, zipfile
from pathlib import Path
import numpy as np
FPS=10
STRANDS={'office left':(3,4,3),'office right':(3,4,3),'landing':(6,4),'master bedroom':(5,5),'bar':(2,3,3,2),'lounge':(3,4,3),'front right':(1,1)}
REVERSE={'office right','lounge','landing','master bedroom','front right'}
BUSHES=['front_left_bush','front_right_left_bush','front_right_right_bush','mid_right','back_right_bush','back_left_bush']
WINDOWS=['lounge_pane_1','lounge_pane_2','lounge_pane_3','bar_pane_1','bar_pane_2','bar_pane_3','bar_pane_4','office_right_pane_3','office_right_pane_2','office_right_pane_1','office_left_pane_3','office_left_pane_2','office_left_pane_1','landing_pane_2','landing_pane_1','master_bedroom_right','master_bedroom_left']
ROUTE=[BUSHES[0],'light_tree',*BUSHES[1:],*WINDOWS[:7],'icicles',*WINDOWS[7:]]
PALETTE=[(255,25,10),(10,230,50),(255,165,35),(30,90,255),(175,35,255),(10,230,50)]
GOLD=(255,180,80)

def norm(s): return ' '.join(s.casefold().split())
def mac(s): return s.lower().replace(':','').replace('-','')
def digest(b): return hashlib.sha256(b).hexdigest()
def points(layout,axes,n):
    p=np.array([[c[a] for a in axes] for c in layout['coordinates']],dtype=float)
    assert p.shape==(n,len(axes)) and np.isfinite(p).all()
    return p

def split(name,n):
    parts=STRANDS[name]; assert n%sum(parts)==0
    order=parts[::-1] if name in REVERSE else parts
    out=np.split(np.arange(n),np.cumsum(order)[:-1]*(n//sum(parts)))
    return out[::-1] if name in REVERSE else out

def unit(v):
    d=np.linalg.norm(v)
    return v/d if d>1e-9 else None

def projection3(p,screen):
    scale=p.std(axis=0); centered=p-p.mean(axis=0)
    a=np.divide(centered,scale,out=np.zeros_like(centered),where=scale>1e-9)
    w=a.T@(screen-screen.mean()); raw=a@w
    if np.ptp(raw)<=1e-9: raise ValueError('Degenerate 3D projection')
    return screen.min()+np.ptp(screen)*(raw-raw.min())/np.ptp(raw)

def load(path):
    with zipfile.ZipFile(path) as z:
        data=json.loads(z.read('corrected-local-evidence-candidate.json'))
        cloud=json.loads(z.read('original-cloud-object-private.json'))
    scene=json.loads(cloud['meta'])['scene']['layouts']
    assert cloud['ledProfile']=='RGBW' and len(scene)==len(data['members'])==15
    parts={mac(x['device']['mac']):x['part'] for x in cloud['devices']}
    assert len(set(parts.values()))==15 and set(parts.values())==set(range(15))
    members={}; coords={}; originals={}; by_name={}; offset=0
    for m in sorted(data['members'],key=lambda x:x['expected']['offset']):
        k=mac(m['mac']); n=m['count']; g=m['expected']; assert g['offset']==offset
        offset+=n; assert g['size']==4900 and g['uid'].lower()==cloud['uuid'].lower()
        s=scene[parts[k]]; assert s['layout']['source']=='3d' and m['layout']['source']=='2d'
        members[k]={'name':m['name'],'mac':k,'ip':m['ip'],'count':n,'offset':g['offset'],'role':g['mode'],'width':4,'group_uid':g['uid']}
        coords[k]=points(m['layout'],'xy',n); originals[k]=points(s['layout'],'xyz',n)
        by_name[norm(m['name'])]=k
    assert offset==4900 and len(members)==15 and sum(m['role']=='master' for m in members.values())==1
    def whole(name):
        k=by_name[name]; return [(k,np.arange(members[k]['count']))]
    zones={
        'front_left_bush':whole('front left bottom')+whole('front left top'),
        'back_right_bush':whole('backr bush bottom')+whole('backr bush top'),
        'back_left_bush':whole('backl bush'),'mid_right':whole('mid right'),
        'light_tree':whole('light tree'),'icicles':whole('icicles')}
    for name in STRANDS:
        k=by_name[name]; pp=split(name,members[k]['count'])
        for i,indices in enumerate(pp):
            if name=='front right': label='front_right_'+('left_bush','right_bush')[i]
            elif name=='master bedroom': label='master_bedroom_'+('left','right')[i]
            else: label=name.replace(' ','_')+'_pane_'+str(i+1)
            zones[label]=[(k,indices)]
    seen=[(k,int(i)) for name in ROUTE for k,ix in zones[name] for i in ix]
    assert len(seen)==len(set(seen))==4900 and set(ROUTE)==set(zones)
    return members,coords,originals,zones

def spatial(members,coords,originals,zones):
    centers=[np.concatenate([coords[k][ix] for k,ix in zones[n]]).mean(axis=0) for n in ROUTE]
    stops=[]
    for j,n in enumerate(ROUTE):
        here=centers[j]; enter=unit(here-centers[j-1]); leave=unit(centers[(j+1)%len(ROUTE)]-here)
        direction=unit(enter+leave) if enter is not None and leave is not None else None
        if direction is None: direction=leave if leave is not None else enter
        if direction is None: direction=np.array([1.,0.])
        garden=n in BUSHES or n=='light_tree'
        pp=[]
        for k,ix in zones[n]:
            vals=coords[k][ix]@direction
            if garden: vals=projection3(originals[k][ix],vals)
            pp.append((k,ix,vals))
        lo=min(v.min() for _,_,v in pp); hi=max(v.max() for _,_,v in pp)
        assert hi>lo
        stops.append(dict(name=n,garden=garden,span=hi-lo,pixels=[(k,ix,(v-lo)/(hi-lo)) for k,ix,v in pp],hold=.35 if garden else .25))
    for j,s in enumerate(stops):
        following=stops[(j+1)%len(stops)]; peers=sorted(t['span'] for t in stops if t['garden']==s['garden']); median=peers[len(peers)//2]
        ratio=(s['span']+following['span'])/(2*median); nominal=1.55 if s['garden'] or following['garden'] else .95
        s['transfer']=nominal*max(.8,min(1.3,math.sqrt(ratio)))
    return stops

def blank(members):return {k:np.zeros((m['count'],4),dtype=np.uint8) for k,m in members.items()}
def solid(members,zones,n,rgb,level=255):
    a=blank(members)
    for k,ix in zones[n]:a[k][ix,1:]=np.rint(np.array(rgb)*level/255).astype(np.uint8)
    return a

def slide(members,stops,t):
    a=blank(members)
    for i,s in enumerate(stops):
        if t<s['hold']+s['transfer']:break
        t-=s['hold']+s['transfer']
    else: i=0;s=stops[0];t=0
    progress=np.clip((t-s['hold'])/s['transfer'],0,1); feather=.17; boundary=progress*(1+2*feather)-feather
    hue=((i+progress)/len(stops)+.10)%1; head=colorsys.hsv_to_rgb(hue,.48,1); base=.58*np.array(GOLD)+.42*255*np.array(head)
    for zone,incoming in [(s,False),(stops[(i+1)%len(stops)],True)]:
        for k,ix,pos in zone['pixels']:
            x=np.clip((boundary-pos+feather)/(2*feather),0,1); swept=x*x*(3-2*x); level=swept if incoming else 1-swept
            wake=np.zeros_like(pos) if incoming else .16*math.sin(math.pi*progress)*np.exp(-((boundary-pos-.24)/.26)**2)
            tail=np.array([colorsys.hsv_to_rgb((hue-p*.65-.18)%1,1,1) for p in pos]); a[k][ix,1:]=np.rint(np.minimum(255,level[:,None]*base+255*tail*wake[:,None])).astype(np.uint8)
    return a

def startup(members,zones,times,route,colors,levels,duration):
    for f in range(round(duration*FPS)):
        i=bisect.bisect_right(times,f/FPS)-1
        yield solid(members,zones,route[i],colors[i],levels[i])

def build(source,dest):
    members,coords,originals,zones=load(source); stops=spatial(members,coords,originals,zones)
    total=sum(s['hold']+s['transfer'] for s in stops)
    dest.mkdir(parents=True,exist_ok=False)
    manifest={'format_version':1,'source_sha256':digest(source.read_bytes()),'group_uid':next(iter(members.values()))['group_uid'],'group_led_count':4900,'controllers':list(members.values()),'movies':[],'route':ROUTE,'window_route':WINDOWS,'geometry':'V3 corrected 2D coordinates; original 3D scene maps associated by cloud device MAC and part index','channel_order':'WRGB'}
    def write(slug,name,frames,notes):
        uid=str(uuid.uuid4()).upper(); buffers={k:bytearray() for k in members}; nf=0
        for a in frames:
            assert set(a)==set(members)
            for k,b in a.items():
                assert b.shape==(members[k]['count'],4) and b.dtype==np.uint8 and not b[:,0].any()
                buffers[k].extend(b.tobytes())
            nf+=1
        assert nf>0
        folder=dest/slug;folder.mkdir();jobs=[]
        for k,m in members.items():
            b=bytes(buffers[k]); assert len(b)==nf*m['count']*4
            filename=f'{slug}/{k}.bin';(dest/filename).write_bytes(b)
            jobs.append({'mac':k,'file':filename,'bytes':len(b),'sha256':digest(b),'metadata':{'name':name,'unique_id':uid,'descriptor_type':'rgbw_raw','leds_per_frame':m['count'],'frames_number':nf,'fps':FPS}})
        manifest['movies'].append({'slug':slug,'name':name,'unique_id':uid,'fps':FPS,'frames':nf,'seconds':nf/FPS,'notes':notes,'jobs':jobs})
        print(name,nf,nf/FPS,sum(len(x) for x in buffers.values()))
    # Uniform samples across the exact loop; no duplicate final frame.
    nf=round(total*FPS)
    write('bush-window-v2','Bush and Window Chase V2', (slide(members,stops,i*total/nf) for i in range(nf)), 'Adaptive spatial wipe, 3D garden geometry, entire bush/pane holds, rainbow wake. Includes tree and icicles.')
    wr=WINDOWS+WINDOWS[-2:0:-1]
    write('window-chase','Window Chase V3',(solid(members,zones,n,GOLD) for n in wr for _ in range(3)),'17 panes forward and back; garden dark; 0.3 seconds per pane.')
    write('house-garden','House and Garden Chase V3',(solid(members,zones,n,GOLD) for n in ROUTE for _ in range(3)),'25 logical objects; crisp steps along same route.')
    bt=[0,1.0333,2.0898,3.158,4.1912,5.2129,6.7338,7.2563,7.8019,8.3012,8.8236,9.3577]
    write('bush-startup','Bush Startup V3',startup(members,zones,bt,BUSHES+BUSHES[::-1],PALETTE+[PALETTE[(i+2)%6] for i in range(6)],[115]*6+[125]*6,10.3561),'Christmas score chapter 02, individual whole bushes, no tree or windows; timing quantised to 0.1s.')
    wt=[0,.5225,1.0333,1.5558,2.0666,2.5891,3.0651,3.5643,4.0635,4.5395,5.062,5.5961,6.014,6.5713,7.0589,7.5581,8.0806,8.5556,9.5667,10.5071,11.4707]
    write('window-startup','Window Startup V3',startup(members,zones,wt,WINDOWS+[WINDOWS[i] for i in (0,5,10,16)],[PALETTE[i%6] for i in range(17)]+[PALETTE[i] for i in (0,1,2,3)],[130]*17+[140]*4,12.9336),'Christmas score chapter 03, individually selected panes; timing quantised to 0.1s.')
    manifest['total_frames_per_controller']=sum(m['frames'] for m in manifest['movies']);manifest['total_raw_bytes']=sum(j['bytes'] for m in manifest['movies'] for j in m['jobs'])
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('Total frames',manifest['total_frames_per_controller'],'bytes',manifest['total_raw_bytes'])
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args();build(a.source,a.output)
