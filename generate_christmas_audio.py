"""Generate Santa narration and festive music, with optional prerecorded natural voice WAVs."""
import argparse
import json
import math
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import wave

RATE=24000
PLAN=Path(__file__).parent/"custom_components/twinkly_area_state/christmas_show.json"
# Jingle Bells (public-domain melody), with original simple accompaniment.
NOTES=(64,64,64,64,64,64,64,67,60,62,64,65,65,65,65,65,64,64,64,64,62,62,64,62,67)
CHORDS=((60,64,67),(60,64,67),(65,69,72),(67,71,74))

def hz(note):
    return 440*2**((note-69)/12)

def render(section,out,voice_tool,ffmpeg,voice_dir=None):
    n=round(section["duration"]*RATE)
    idx=int(section["id"][:2])
    rng=random.Random(section["id"])
    with tempfile.TemporaryDirectory() as folder:
        tmp=Path(folder)
        speech=[]
        if section["voice"]:
            recorded=voice_dir/(section["id"]+".wav") if voice_dir else None
            source=recorded if recorded and recorded.exists() else tmp/"voice.wav"
            if source==tmp/"voice.wav":
                if not voice_tool:
                    raise ValueError("Missing voice WAV: "+section["id"])
                subprocess.run([voice_tool,"-v","en-gb+m3","-s","155","-p","32","-a","110","-w",str(source),section["voice"]],check=True)
            subprocess.run([ffmpeg,"-loglevel","error","-y","-i",str(source),"-af","highpass=f=75,lowpass=f=5000,acompressor=threshold=0.12:ratio=2,volume=1.2","-ar",str(RATE),"-ac","1","-f","s16le",str(tmp/"voice.raw")],check=True)
            speech=[v[0]/32768 for v in struct.iter_unpack("<h",(tmp/"voice.raw").read_bytes())]
            available=n-round(.35*RATE)
            if len(speech)>available:
                speed=len(speech)/available
                if speed>2:
                    raise ValueError("Narration too long: "+section["id"])
                subprocess.run([ffmpeg,"-loglevel","error","-y","-f","s16le","-ar",str(RATE),"-ac","1","-i",str(tmp/"voice.raw"),"-af",f"atempo={speed*1.01:.5f}","-f","s16le",str(tmp/"fitted.raw")],check=True)
                speech=[v[0]/32768 for v in struct.iter_unpack("<h",(tmp/"fitted.raw").read_bytes())]
            if len(speech)>available:
                raise ValueError("Narration does not fit: "+section["id"])
        with wave.open(str(tmp/"mix.wav"),"wb") as w:
            w.setparams((1,2,RATE,0,"NONE","not compressed"))
            buf=bytearray()
            for i in range(n):
                t=i/RATE
                beat=t%.5
                note=NOTES[int(t*2)%len(NOTES)]
                f=hz(note)
                env=math.exp(-beat*5)
                melody=env*(math.sin(2*math.pi*f*beat)+.34*math.sin(2*math.pi*f*2*beat)+.16*math.sin(2*math.pi*f*3*beat))
                chord=CHORDS[int(t/2)%len(CHORDS)]
                harmony=sum(math.sin(2*math.pi*hz(k-12)*t)+.18*math.sin(4*math.pi*hz(k-12)*t) for k in chord)/3
                bell_t=t%.25
                bell=math.exp(-bell_t*29)*(.5*math.sin(2*math.pi*2300*bell_t)+.25*math.sin(2*math.pi*3180*bell_t)+.25*rng.uniform(-1,1))
                chime=math.exp(-(t%2)*6)*math.sin(2*math.pi*hz(note+12)*(t%2))
                if idx==8:
                    v=0
                elif idx in (6,7):
                    v=.07*math.sin(2*math.pi*(440*t-35*t*t))+.025*rng.uniform(-1,1)*env+.06*bell
                else:
                    intensity=.95 if idx==11 else (.7 if idx in (5,9,10,12) else .38)
                    if idx==11:
                        intensity*=.65+.35*min(1,t/10)
                    v=intensity*(.19*melody+.09*harmony+.11*bell+.065*chime)
                si=i-int(.2*RATE)
                if 0<=si<len(speech):
                    echo=speech[si-int(.045*RATE)]*.10 if si>=int(.045*RATE) else 0
                    v=v*.32+.78*speech[si]+echo
                fade=min(1,t/.035,(n-i)/RATE/.08)
                buf.extend(struct.pack("<h",round(max(-1,min(1,v*fade))*32767)))
            w.writeframes(buf)
        subprocess.run([ffmpeg,"-loglevel","error","-y","-i",str(tmp/"mix.wav"),"-b:a","128k",str(out/(section["id"]+".mp3"))],check=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path("christmas_audio"))
    parser.add_argument("--voice-dir",type=Path,help="Optional folder of prerecorded Santa WAVs named by section")
    args=parser.parse_args()
    voice=shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg or (not voice and not args.voice_dir):
        parser.error("Install ffmpeg and espeak-ng/espeak, or provide voice WAVs")
    args.output.mkdir(parents=True,exist_ok=True)
    for section in json.loads(PLAN.read_text(encoding="utf-8"))["sections"]:
        render(section,args.output,voice,ffmpeg,args.voice_dir)
        print(section["id"],section["duration"])

if __name__=="__main__":
    main()
