#!/usr/bin/env python3
"""Build the continuous version 2 soundtrack from the approved score and assets.

Requires ffmpeg and numpy. Supply the credited Christmas music recording and
one WAV for each narration id. Delivered Santa stems can be reused unchanged.
This command never generates paid speech, starts the show, or contacts HA.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--music',type=Path,required=True,help='Kevin MacLeod We Wish You source recording')
    parser.add_argument('--voice-dir',type=Path,required=True,help='Folder of the twelve timed narration WAV stems')
    parser.add_argument('--output',type=Path,default=Path('christmas_audio'))
    parser.add_argument('--plan',type=Path,default=ROOT/'custom_components/twinkly_area_state/christmas_show.json')
    args=parser.parse_args()
    plan=json.loads(args.plan.read_text(encoding='utf-8'))
    narration=[]
    for line in plan['narration']:
        path=args.voice_dir/(line['id']+'.wav')
        if not path.is_file(): parser.error('Missing narration stem: '+str(path))
        narration.append({**line,'path':str(path.resolve())})
    if not args.music.is_file(): parser.error('Music recording not found: '+str(args.music))
    config={'music_path':str(args.music.resolve()),'duration':plan['soundtrack']['duration'],
            'output_dir':str(args.output.resolve()),'narration':narration,
            'music_gain_db':-3.5,'voice_lufs':-18,'duck_db':14,'master_lufs':-18,'master_lra':14}
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'mix.json'
        path.write_text(json.dumps(config),encoding='utf-8')
        subprocess.run([sys.executable,str(ROOT/'tools/build_christmas_soundtrack.py'),str(path)],check=True)
    source=args.output/'christmas_show_master.mp3'
    destination=args.output/plan['soundtrack']['file']
    destination.write_bytes(source.read_bytes())
    print('Install:',destination)

if __name__=='__main__': main()
