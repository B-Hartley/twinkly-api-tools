#!/usr/bin/env python3
"""Reproducibly mix a continuous Christmas score and precisely placed narration.

Input JSON: {music_path, duration, output_dir, narration:[{id,path,at,text}],
 music_gain_db:-3.5, voice_lufs:-18, duck_db:14, ...}.
'at' is the audible onset (leading/trailing digital silence is trimmed).
No resampling-rate tricks, pitch shifts, word edits or tempo edits are applied.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import numpy as np

SR=48000

def run(*args):
    return subprocess.run(args,check=True,capture_output=True)

def decode(path, channels=2):
    raw=run('ffmpeg','-v','error','-i',str(path),'-vn','-ac',str(channels),'-ar',str(SR),'-f','f32le','pipe:1').stdout
    return np.frombuffer(raw,dtype='<f4').reshape(-1,channels).copy()

def loudness(path, target=-18, lra=14):
    result=run('ffmpeg','-hide_banner','-nostats','-i',str(path),'-vn','-af',f'loudnorm=I={target}:TP=-2:LRA={lra}:print_format=json','-f','null','-')
    return json.loads(re.findall(r'\{[^}]+\}',result.stderr.decode())[-1])

def write_wav(path,x):
    process=subprocess.run(['ffmpeg','-v','error','-y','-f','f32le','-ac',str(x.shape[1]),'-ar',str(SR),'-i','pipe:0','-c:a','pcm_s24le',str(path)],input=x.astype('<f4').tobytes(),capture_output=True)
    if process.returncode:raise RuntimeError(process.stderr.decode())

def trim_voice(x):
    # Use 8ms RMS blocks to retain quiet consonants and breaths but remove dead lead-in.
    mono=np.mean(x,axis=1)
    n=384
    padded=np.pad(mono,(0,(-len(mono))%n))
    rms=np.sqrt(np.mean(padded.reshape(-1,n)**2,axis=1))
    thresh=max(10**(-57/20),float(rms.max())*10**(-39/20))
    active=np.flatnonzero(rms>thresh)
    if not len(active):raise ValueError('Narration contains no audible speech')
    first=max(0,int(active[0])*n-192)
    last=min(len(x),(int(active[-1])+1)*n+int(.08*SR))
    return x[first:last],first/SR,(int(active[0])*n-first)/SR

def main():
    args=argparse.ArgumentParser();args.add_argument('config',type=Path);opt=args.parse_args()
    cfg=json.loads(opt.config.read_text())
    def resolve(value):
        path=Path(value).expanduser()
        return path if path.is_absolute() else opt.config.resolve().parent/path
    out=resolve(cfg['output_dir']);out.mkdir(parents=True,exist_ok=True)
    duration=float(cfg['duration']);frames=round(duration*SR)
    music_source=resolve(cfg['music_path'])
    music=decode(music_source);track=np.zeros((frames,2),dtype=np.float32)
    start=round(float(cfg.get('music_at',0))*SR)
    count=min(len(music),frames-start);track[start:start+count]=music[:count]
    track*=10**(float(cfg.get('music_gain_db',-3.5))/20)
    envelope=np.ones(frames,dtype=np.float32)
    voices=np.zeros_like(track);spans=[]
    for entry in cfg['narration']:
        source=resolve(entry['path']);voice=decode(source)
        voice,removed_lead,onset=trim_voice(voice)
        trimmed=out/(entry['id']+'_dry.wav');write_wav(trimmed,voice)
        # A fixed gain preserves Santa's emphasis. Low-cut removes rumble, not character.
        measured=loudness(trimmed)
        gain=float(cfg.get('voice_lufs',-18))-float(measured['input_i'])
        gain=min(gain,-2.5-float(measured['input_tp']))
        filtered=run('ffmpeg','-v','error','-i',str(trimmed),'-af',f'highpass=f=65,volume={gain:.6f}dB','-ac','2','-ar',str(SR),'-f','f32le','pipe:1').stdout
        voice=np.frombuffer(filtered,dtype='<f4').reshape(-1,2).copy()
        # A short, very low-level room reflection avoids an unnaturally detached dry voice.
        if cfg.get('voice_room',True):
            tail=np.zeros((len(voice)+round(.18*SR),2),dtype=np.float32);tail[:len(voice)]=voice
            for delay,amount in [(.043,.025),(.079,.018),(.127,.01)]:
                d=round(delay*SR);tail[d:d+len(voice)]+=voice*amount
            voice=tail
        at=float(entry['at']);place=round((at-onset)*SR)
        if place<0 or place+len(voice)>frames:raise ValueError(f"Narration {entry['id']} lies outside master duration")
        end=(place+len(voice))/SR
        if spans and at<spans[-1]['voice_end']+.12:raise ValueError('Narration clips overlap')
        voices[place:place+len(voice)]+=voice
        # Quiet opening instrumentation needs less ducking than the full-band finale.
        # Keep Santa about 10 dB above the local music RMS, with gentle 3–14 dB ducking.
        voice_rms=float(np.sqrt(np.mean(voice**2)))
        music_rms=float(np.sqrt(np.mean(track[place:place+len(voice)]**2)))
        desired_duck=20*math.log10(max(music_rms,1e-9)/max(voice_rms,1e-9))+10
        duck_db=min(float(cfg.get('duck_db',14)), max(3.0,desired_duck))
        # Smooth equal-slope duck, held across the whole phrase, keeps every word clear.
        times=np.array([max(0,at-.20),max(0,at-.035),end+.06,end+.55])
        levels=np.array([1,10**(-duck_db/20),10**(-duck_db/20),1])
        lo=max(0,int(times[0]*SR));hi=min(frames,math.ceil(times[-1]*SR))
        u=np.arange(lo,hi)/SR
        envelope[lo:hi]=np.minimum(envelope[lo:hi],np.interp(u,times,levels))
        spans.append({**entry,'voice_onset':at,'voice_end':round(end,5),'duration':round(len(voice)/SR,5),'trimmed_leading_seconds':round(removed_lead,5),'voice_input_lufs':measured['input_i'],'applied_gain_db':round(gain,5),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'music_duck_db':round(duck_db,3),'voice_music_rms_gap_db':round(20*math.log10(max(voice_rms,1e-9)/max(music_rms,1e-9))+duck_db,3)})
    track*=envelope[:,None]
    premaster=track+voices
    # Seamless start/end with a very short opening fade; final tail is already scored.
    fade=min(round(.025*SR),frames);premaster[:fade]*=np.linspace(0,1,fade)[:,None]
    fade=min(round(.30*SR),frames);premaster[-fade:]*=np.linspace(1,0,fade)[:,None]
    pre=out/'christmas_show_premaster.wav';write_wav(pre,premaster)
    measured=loudness(pre, cfg.get('master_lufs',-18),cfg.get('master_lra',14))
    # Constrain a single fixed gain by true-peak headroom. Never fall back to
    # loudnorm's dynamic mode just to chase a fraction of a decibel in loudness.
    master_gain=min(float(cfg.get('master_lufs',-18))-float(measured['input_i']),
                    -2.0-float(measured['input_tp']))
    master=out/'christmas_show_master.wav'
    run('ffmpeg','-v','error','-y','-i',str(pre),'-af',f'volume={master_gain:.6f}dB',
        '-ar',str(SR),'-c:a','pcm_s24le',str(master))
    normdata={'normalization_type':'linear, constrained by true-peak headroom',
              'gain_db':round(master_gain,6),'input_i':measured['input_i'],
              'input_tp':measured['input_tp'],'input_lra':measured['input_lra'],
              'output_i':round(float(measured['input_i'])+master_gain,2),
              'output_tp':round(float(measured['input_tp'])+master_gain,2),
              'output_lra':measured['input_lra']}
    mp3=out/'christmas_show_master.mp3'
    credit=cfg.get('credit','We Wish you a Merry Christmas — Kevin MacLeod (incompetech.com), CC BY 4.0. Adapted with Santa narration, ducking and mastering.')
    run('ffmpeg','-v','error','-y','-i',str(master),'-codec:a','libmp3lame','-b:a','320k','-ar',str(SR),'-metadata','title=Santa’s Christmas Lights','-metadata','artist=Christmas Show Production','-metadata','comment='+credit,str(mp3))
    qc=loudness(mp3)
    if float(qc['input_tp']) > -1.5:
        raise ValueError('Encoded MP3 exceeds the -1.5 dBTP delivery ceiling')
    preview=out/'christmas_show_preview.mp3'
    run('ffmpeg','-v','error','-y','-ss','40','-i',str(master),'-t','30','-af',
        'afade=t=in:st=0:d=0.08,afade=t=out:st=29.65:d=0.35',
        '-c:a','libmp3lame','-b:a','320k','-metadata',
        'title=Christmas show preview — full show 00:40 to 01:10',str(preview))
    manifest={'voice':cfg.get('voice',{}),'duration':duration,'sample_rate':SR,'channels':2,'music_path':cfg['music_path'],'music_sha256':hashlib.sha256(music_source.read_bytes()).hexdigest(),'music_at':cfg.get('music_at',0),'music_gain_db':cfg.get('music_gain_db',-3.5),'master_loudness':normdata,'encoded_mp3_loudness':{'integrated_lufs':float(qc['input_i']),'true_peak_dbtp':float(qc['input_tp']),'loudness_range_lu':float(qc['input_lra'])},'narration':spans,'master_sha256':hashlib.sha256(master.read_bytes()).hexdigest(),'mp3_sha256':hashlib.sha256(mp3.read_bytes()).hexdigest(),'preview':{'start_in_master':40,'duration':30,'path':str(preview)}}
    (out/'production_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'master':str(master),'mp3':str(mp3),'manifest':str(out/'production_manifest.json'),'master_loudness':normdata,'mp3_loudness':qc,'narration':spans},indent=2))
if __name__=='__main__':main()
