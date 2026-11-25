"""Generate MP3 assets using Python stdlib, espeak-ng (or espeak), and ffmpeg."""
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

def render(section,out,voice_tool,ffmpeg):
    n=section["duration"]*RATE
    rng=random.Random(section["id"])
    idx=int(section["id"][:2])
    notes=[60,64,67,72,69,67,64,62,65,69,72,76,74,72,67,65]
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp)
        speech=[]
        if section["voice"]:
            subprocess.run([voice_tool,"-v","en-gb","-s","145","-p","38","-w",str(tmp/"voice.wav"),section["voice"]],check=True)
            subprocess.run([ffmpeg,"-loglevel","error","-y","-i",str(tmp/"voice.wav"),"-ar",str(RATE),"-ac","1","-f","s16le",str(tmp/"voice.raw")],check=True)
            raw=(tmp/"voice.raw").read_bytes()
            speech=[v[0]/32768 for v in struct.iter_unpack("<h",raw)]
        with wave.open(str(tmp/"mix.wav"),"wb") as w:
            w.setparams((1,2,RATE,0,"NONE","not compressed"))
            buf=bytearray()
            for i in range(n):
                t=i/RATE
                p=t%0.5
                env=math.exp(-p*8)
                if idx==8:
                    v=0
                elif idx in (5,11):
                    f=440*2**((notes[int(t*2)%len(notes)]-69)/12)
                    v=.17*env*(math.sin(2*math.pi*f*t)+.35*math.sin(4*math.pi*f*t))
                    v+=.11*math.sin(2*math.pi*55*t)*math.exp(-p*12)
                    v+=.04*rng.uniform(-1,1)*math.exp(-p*25)
                elif idx in (6,7):
                    v=.12*math.sin(2*math.pi*(440*t-35*t*t))+.06*rng.uniform(-1,1)
                elif idx==9:
                    v=.16*math.sin(2*math.pi*60*t)*math.exp(-(t%1)*12)
                else:
                    v=.045*math.sin(2*math.pi*55*t)+.08*env*math.sin(2*math.pi*(440+110*(int(t*2)%4))*t)
                si=i-int(.25*RATE)
                if 0<=si<len(speech):
                    v=v*.35+.65*speech[si]
                fade=min(1,t/.04,(n-i)/RATE/.08)
                buf.extend(struct.pack("<h",round(max(-1,min(1,v*fade))*32767)))
            w.writeframes(buf)
        subprocess.run([ffmpeg,"-loglevel","error","-y","-i",str(tmp/"mix.wav"),"-b:a","96k",str(out/(section["id"]+".mp3"))],check=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path("christmas_audio"))
    args=parser.parse_args()
    voice=shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg=shutil.which("ffmpeg")
    if not voice or not ffmpeg:
        parser.error("Install espeak-ng (or espeak) and ffmpeg first")
    args.output.mkdir(parents=True,exist_ok=True)
    for section in json.loads(PLAN.read_text(encoding="utf-8"))["sections"]:
        render(section,args.output,voice,ffmpeg)
        print(section["id"],section["duration"])

if __name__=="__main__":
    main()
