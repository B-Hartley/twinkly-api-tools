#!/usr/bin/env python3
"""Rebuild a separate V2 MP3 with the complete 'winter magic' word ending.

No speech is generated. The original British Santa source is required. The
published V2 programme/timeline and every other speech cut remain unchanged.
The old phrase cut at source 20.967s removes audible speech through 21.10s;
21.300s lies in the measured quiet before the next phrase. This explicit
repair is source-hash guarded, so it cannot silently cut a different take.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from build_christmas_soundtrack import SR, decode, write_wav
from build_house_chase_audio import mix, prepare_music, split_take

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE = "36a328555b75e05245cda9025b34e0690e55bfaa6f6e23f60fee54da43a7a5b2"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--music", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.voice.read_bytes()).hexdigest() != EXPECTED_SOURCE:
        parser.error("This is not the measured original V2 voice take; inspect its word boundaries first")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    narration = json.loads((ROOT / "tools/house_chase_narration.json").read_text())
    response = json.loads(args.response.read_text())
    stems = split_take(response, args.voice, narration, out / "voice")
    entry = next(line for line in stems if line["id"] == "05_icicles")
    old_cut, new_cut = entry["source_end"], 21.300
    clip = decode(args.voice)[round(entry["source_start"] * SR):round(new_cut * SR)].copy()
    ramp = round(.003 * SR)
    clip[:ramp] *= np.linspace(0, 1, ramp)[:, None]
    clip[-ramp:] *= np.linspace(1, 0, ramp)[:, None]
    path = Path(entry["path"])
    write_wav(path, clip)
    entry.update(source_end=new_cut, duration=len(clip) / SR,
                 sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    music = out / "show_music_prepared.wav"
    prepare_music(args.music, music, 140)
    credit = ("We Wish you a Merry Christmas — Kevin MacLeod (incompetech.com), CC BY 4.0. "
              "Adapted with British Santa narration, theatrical volume dip, original bell sparkles, "
              "ducking and mastering. AI-generated voice: HeyGen Willoughby. "
              "Icicles phrase word tail restored from original generated take.")
    config = {"music_path": str(music), "duration": 140, "output_dir": str(out / "mix"),
              "narration": stems, "voice": narration["voice"], "music_gain_db": -3.5,
              "voice_lufs": -18, "duck_db": 14, "master_lufs": -18,
              "master_lra": 14, "credit": credit}
    name = "christmas_show_house_chase_v1_magic_fixed.mp3"
    rendered = mix(config, name)
    delivered = out / name
    delivered.write_bytes(rendered.read_bytes())
    manifest = {"change": "Restore the complete magic word ending in the Icicles phrase; no new speech generated",
                "original_source_cut_seconds": old_cut, "revised_source_cut_seconds": new_cut,
                "added_source_tail_seconds": new_cut - old_cut,
                "music_and_other_phrases": "unchanged", "show_duration": 140, "phrase_onset": 40,
                "original_source_sha256": EXPECTED_SOURCE,
                "repaired_mp3_sha256": hashlib.sha256(delivered.read_bytes()).hexdigest(),
                "install_note": "Optional replacement for V2 only: preserve existing christmas_show_house_chase_v1.mp3, then copy this file with that original name. V1 and V3 are unaffected."}
    (out / "magic_repair_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(delivered)


if __name__ == "__main__":
    main()
