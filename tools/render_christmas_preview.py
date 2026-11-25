#!/usr/bin/env python3
"""Render a timing schematic from the production Christmas show plan.

Requires Pillow and ffmpeg. This is a logical-zone visualisation, not a photo,
map, LED-level simulation, or capture of actual hardware. Native stored movies
are labelled placeholders because their pixel content belongs to controllers.

python tools/render_christmas_preview.py --plan path/to/christmas_show.json \
    --audio path/to/christmas_show_v2.mp3 --voice-manifest production_manifest.json \
    --output christmas_show_schematic.mp4
"""
import argparse
import bisect
import colorsys
import hashlib
import json
import math
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageFont

W,H=960,540
BG=(9,16,29);PANEL=(16,26,43);LINE=(41,56,75)
WHITE=(235,241,246);MUTED=(147,166,187);GOLD=(247,198,105)
FONTS={}

def font(size,bold=False):
    key=(size,bold)
    if key not in FONTS:
        name='DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
        path=Path('/usr/share/fonts/truetype/dejavu')/name
        candidates=[str(path),name,'arialbd.ttf' if bold else 'arial.ttf']
        for candidate in candidates:
            try:
                FONTS[key]=ImageFont.truetype(candidate,size)
                break
            except OSError:
                continue
        else:
            try:
                FONTS[key]=ImageFont.load_default(size=size)
            except TypeError:
                FONTS[key]=ImageFont.load_default()
    return FONTS[key]

def label(draw,text,x,y,size=14,color=WHITE,bold=False,anchor=None):
    draw.text((x,y),text,font=font(size,bold),fill=color,anchor=anchor)

def clock(t):
    return f'{int(t)//60:02}:{int(t)%60:02}'

def wrapped(draw,text,max_width,size=17):
    lines=[];line=''
    for word in text.split():
        candidate=(line+' '+word).strip()
        if line and draw.textlength(candidate,font=font(size))>max_width:
            lines.append(line);line=word
        else:line=candidate
    if line:lines.append(line)
    return lines

def make_layout(plan):
    windows=plan['zones']['windows'];nodes={}
    groups=[('Office left',[x for x in windows if 'office_left_' in x]),
            ('Office right',[x for x in windows if 'office_right_' in x]),
            ('Master bedroom',[x for x in windows if 'master_bedroom_' in x]),
            ('Landing',[x for x in windows if 'landing_' in x]),
            ('Lounge',[x for x in windows if 'lounge_' in x]),
            ('Bar',[x for x in windows if 'bar_' in x])]
    group_labels=[]
    for i,(name,ids) in enumerate(groups):
        col=i%3;row=i//3;x=46+col*202;y=146+row*89
        group_labels.append((name,x+83,y-24))
        gap=6;pw=(168-gap*(len(ids)-1))//len(ids)
        for n,entity in enumerate(ids):
            box=(x+n*(pw+gap),y,x+n*(pw+gap)+pw,y+47)
            nodes[entity]={'shape':'pane','box':box,'label':str(n+1)}
    bush_names={'front_left_bush':'Front left\nwhole bush','back_left_bush':'Back left',
                'back_right_bush':'Back right\nwhole bush','mid_right':'Mid right',
                'front_right_left_bush':'Front right\nleft bush','front_right_right_bush':'Front right\nright bush'}
    for i,entity in enumerate(plan['zones']['bushes']):
        cx=93+i*155
        nodes[entity]={'shape':'bush','box':(cx-46,344,cx+46,384),
                       'name':bush_names.get(entity.removeprefix('light.twinkly_'),entity)}
    for entity in plan['zones']['tree']:
        nodes[entity]={'shape':'tree','box':(705,163,785,275),'name':'Tree'}
    for entity in plan['zones']['icicles']:
        nodes[entity]={'shape':'icicles','box':(817,177,907,266),'name':'Icicles'}
    expected=set().union(*[set(plan['zones'][kind]) for kind in ('windows','bushes','tree','icicles')])
    if set(nodes)!=expected:raise ValueError('Schematic must represent every logical show zone exactly once')
    return nodes,group_labels

def mask_for(node):
    m=Image.new('L',(W,H));d=ImageDraw.Draw(m);x0,y0,x1,y1=node['box']
    if node['shape']=='pane':d.rounded_rectangle(node['box'],radius=5,fill=255)
    elif node['shape']=='bush':
        cy=(y0+y1)/2
        d.ellipse((x0,y0+8,x0+48,y1),fill=255)
        d.ellipse((x1-48,y0+8,x1,y1),fill=255)
        d.ellipse((x0+22,y0-4,x1-22,y1),fill=255)
    elif node['shape']=='tree':
        cx=(x0+x1)/2
        d.polygon([(cx,y0),(x0+12,y0+43),(x0+22,y0+43),(x0,y1-16),(x1,y1-16),(x1-22,y0+43),(x1-12,y0+43)],fill=255)
        d.rectangle((cx-5,y1-16,cx+5,y1),fill=255)
    elif node['shape']=='icicles':
        d.line((x0,y0,x1,y0),fill=255,width=4)
        for n in range(9):
            x=x0+n*(x1-x0)/8;length=[45,70,35,64,83,42,67,32,53][n]
            d.line((x,y0,x,y0+length),fill=255,width=4)
    bounds=m.getbbox()
    return m.crop(bounds),bounds

def base_frame(plan,nodes,group_labels):
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
    label(d,"Santa’s Christmas Magic",26,15,25,bold=True)
    label(d,'SCHEMATIC · TIMING REVIEW',932,24,11,GOLD,True,'ra')
    label(d,'17 window panes  ·  6 bushes  ·  tree and icicles',28,51,13,MUTED)
    d.rounded_rectangle((25,116,658,309),radius=12,fill=PANEL,outline=LINE)
    d.rounded_rectangle((678,116,934,309),radius=12,fill=PANEL,outline=LINE)
    label(d,'ADDITIONAL ZONES',806,127,11,MUTED,True,'ma')
    for name,x,y in group_labels:label(d,name,x,y,13,MUTED,False,'ma')
    for entity,node in nodes.items():
        if node['shape'] in ('tree','icicles'):
            x0,y0,x1,y1=node['box'];label(d,node['name'],(x0+x1)/2,284,13,MUTED,False,'ma')
        if node['shape']=='bush':
            x0,y0,x1,y1=node['box']
            for k,line in enumerate(node['name'].split('\n')):
                label(d,line,(x0+x1)/2,391+15*k,11,MUTED,False,'ma')
    d.rounded_rectangle((25,426,934,489),radius=10,fill=PANEL)
    label(d,'SANTA',40,437,11,GOLD,True)
    label(d,'Logical layout · relative brightness · not live footage',28,520,10,MUTED)
    return im

def empty_state(nodes):
    return {key:{'on':False,'brightness':255,'rgb':(255,255,255),'effect':'Solid'} for key in nodes}

def apply_cue(states,cue,movie):
    ids=list(states) if 'light.twinkly_front' in cue['targets'] else cue['targets']
    if cue.get('stored_movie'):
        return int(cue.get('movie_index',0))+1
    for entity in ids:
        if entity not in states:raise ValueError('Unmapped preview target: '+entity)
        st=states[entity]
        if cue.get('off') or cue.get('brightness',255)<=0:
            st['on']=False
            if entity in ids and 'light.twinkly_front' in cue['targets']:movie=None
            continue
        movie=None;st['on']=True
        st['brightness']=cue.get('brightness',st['brightness'])
        if 'rgb' in cue:
            st['rgb']=tuple(cue['rgb']);st['effect']='Solid'
        if 'effect' in cue:st['effect']=cue['effect']
    return movie

def paint_zone(im,mask,node,state):
    x0,y0,x1,y1=node['box'];d=ImageDraw.Draw(im)
    mask,bounds=mask
    if not state['on']:
        im.paste((29,41,57),bounds,mask)
    elif 'rainbow' in state['effect'].lower():
        stripe=Image.new('RGB',mask.size);sd=ImageDraw.Draw(stripe)
        for y in range(mask.size[1]):
            hue=max(0,min(1,(y+bounds[1]-y0)/max(1,y1-y0)))
            if 'reversed' in state['effect'].lower():hue=1-hue
            c=tuple(round(v*state['brightness']) for v in colorsys.hsv_to_rgb(hue,.92,1))
            sd.line((0,y,mask.size[0],y),fill=c)
        im.paste(stripe,(bounds[0],bounds[1]),mask)
    else:
        bri=state['brightness']/255
        c=tuple(round(v*bri) for v in state['rgb'])
        im.paste(c,bounds,mask)
    if node['shape']=='pane':
        d.rounded_rectangle(node['box'],radius=5,outline=(93,111,132) if state['on'] else LINE,width=1)
        label(d,node['label'],(x0+x1)/2,(y0+y1)/2,11,WHITE if state['on'] else MUTED,False,'mm')

def main():
    a=argparse.ArgumentParser();a.add_argument('--plan',type=Path,required=True)
    a.add_argument('--audio',type=Path);a.add_argument('--voice-manifest',type=Path)
    a.add_argument('--output',type=Path,required=True);a.add_argument('--fps',type=int,default=12)
    a.add_argument('--snapshots',default='10,21,63,80,89,105,131')
    args=a.parse_args();plan=json.loads(args.plan.read_text());duration=float(plan['soundtrack']['duration'])
    if args.fps<1:raise ValueError('fps must be positive')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    nodes,labels=make_layout(plan);base=base_frame(plan,nodes,labels);masks={key:mask_for(n) for key,n in nodes.items()}
    cues=sorted((float(s['start'])+float(c['at']),si,ci,c) for si,s in enumerate(plan['sections']) for ci,c in enumerate(s['cues']))
    sections=plan['sections'];starts=[float(s['start']) for s in sections]
    voices=plan.get('narration',[]);voice_metadata={}
    if args.voice_manifest:
        voice_document=json.loads(args.voice_manifest.read_text())
        voice_metadata=voice_document.get('voice',{})
        voiced={n['id']:n for n in voice_document['narration']}
        voices=[{**n,'voice_end':voiced.get(n['id'],{}).get('voice_end',voiced.get(n['id'],{}).get('end',n['until']))} for n in voices]
    snapshots={round(float(x)*args.fps):float(x) for x in args.snapshots.split(',') if x}
    silent=args.output.with_name(args.output.stem+'_silent.mp4') if args.audio else args.output
    command=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(args.fps),'-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',str(silent)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    state=empty_state(nodes);movie=None;index=0;counts=[]
    frames=round(duration*args.fps)
    try:
        for frame in range(frames):
            t=frame/args.fps
            while index<len(cues) and cues[index][0]<=t+1e-8:
                movie=apply_cue(state,cues[index][3],movie);index+=1
            im=base.copy();d=ImageDraw.Draw(im)
            sec=sections[max(0,bisect.bisect_right(starts,t)-1)]
            label(d,sec['title'],28,82,19,GOLD,True)
            label(d,f'{clock(t)} / {clock(duration)}',932,84,17,WHITE,True,'ra')
            for key,node in nodes.items():paint_zone(im,masks[key],node,state[key])
            if movie is not None:
                # Cover the schematic rather than presenting invented controller pixels.
                d.rounded_rectangle((25,116,934,419),radius=16,fill=(12,23,39),outline=GOLD,width=2)
                label(d,f'STORED MOVIE · SLOT {movie}',480,191,27,GOLD,True,'ma')
                label(d,'Native playback on the Twinkly controllers',480,239,19,WHITE,False,'ma')
                label(d,'Movie imagery is not simulated in this preview.',480,277,15,MUTED,False,'ma')
                label(d,'The soundtrack continues on the same clock.',480,306,14,MUTED,False,'ma')
            current=next((v for v in voices if float(v['at'])<=t<float(v.get('voice_end',v['until']))),None)
            d.rectangle((31,432,97,457),fill=PANEL)
            label(d,'SANTA' if current else 'MUSIC',40,437,11,GOLD,True)
            caption=current['text'] if current else plan['music']['title']+' — '+plan['music']['artist']
            lines=wrapped(d,caption,805,16)
            for k,line in enumerate(lines[:2]):label(d,line,104,434+k*23,16,WHITE if current else MUTED)
            x0,y0,x1,y1=28,505,932,511
            d.rounded_rectangle((x0,y0,x1,y1),radius=3,fill=LINE)
            d.rounded_rectangle((x0,y0,max(x0+1,x0+(x1-x0)*t/duration),y1),radius=3,fill=GOLD)
            for s in sections:
                x=x0+(x1-x0)*float(s['start'])/duration
                d.line((x,y0-3,x,y1+3),fill=MUTED,width=1)
            if frame in snapshots:
                im.save(args.output.with_name(args.output.stem+f'_{int(snapshots[frame]):03d}s.png'))
                counts.append({'at':t,'active_zones':sum(v['on'] for v in state.values()),'movie_placeholder':movie})
            process.stdin.write(im.tobytes())
        process.stdin.close();stderr=process.stderr.read();code=process.wait()
        if code:raise RuntimeError(stderr.decode())
    except BaseException:
        process.kill();process.wait();raise
    if args.audio:
        music=plan['music']
        voice_name=voice_metadata.get('voice','synthetic Santa-style character') if isinstance(voice_metadata,dict) else str(voice_metadata)
        credit=(f"Music: {music['title']} by {music['artist']} (incompetech.com). "
                f"{music['license']}: {music['license_url']} Source: {music['source']}. "
                f"Adapted with narration, ducking and mastering. Synthetic narration: {voice_name}. "
                "Schematic timing preview; native movie pixels are not simulated.")
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(silent),'-i',str(args.audio),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(duration),'-metadata','title=Santa’s Christmas Magic — schematic timing preview','-metadata','comment='+credit,'-movflags','+faststart',str(args.output)],check=True)
    result={'output':str(args.output),'silent_video':str(silent),'duration':duration,'fps':args.fps,'frames':frames,'size':[W,H],'plan_sha256':hashlib.sha256(args.plan.read_bytes()).hexdigest(),'logical_zone_count':len(nodes),'processed_cue_count':index,'total_cue_count':len(cues),'snapshot_states':counts,'schematic_only':True,'native_movie_pixels_simulated':False}
    args.output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
