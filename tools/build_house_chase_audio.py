#!/usr/bin/env python3
"""Build the optional British Santa soundtrack from one timestamped voice take.

Requires ffmpeg and numpy; no network calls, speech generation or live playback.
The source take is unchanged. Phrases are cut at their measured boundaries and
placed against the show clock without pitch or tempo alteration. This also
builds a ten-second countdown with the same five spoken digits at 3..7 seconds.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from build_christmas_soundtrack import SR, decode, trim_voice, write_wav

ROOT = Path(__file__).resolve().parents[1]


def split_take(response, source, narration, out):
    characters = response["original_characters"]
    expected = "\n\n".join(line["text"] for line in narration["narration"])
    if "".join(char["text"] for char in characters) != expected:
        raise ValueError("Returned narration transcript does not match the agreed text")
    if any(len(char["text"]) != 1 for char in characters):
        raise ValueError("Unexpected character alignment format")
    audio = decode(source)
    total = len(audio) / SR
    cursor = 0
    stems = []
    out.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(narration["narration"]):
        end_index = cursor + len(line["text"])
        selected = characters[cursor:end_index]
        spoken = [char for char in selected if char["text"].isalnum()]
        first = float(spoken[0]["start_time"])
        last = max(float(char["end_time"]) for char in spoken)
        following = (float(characters[end_index + 2]["start_time"])
                     if i + 1 < len(narration["narration"]) else total)
        start = max(0, first - .060)
        end = min(following - .045, last + .160, total)
        clip = audio[round(start * SR):round(end * SR)].copy()
        ramp = min(round(.003 * SR), len(clip) // 4)
        clip[:ramp] *= np.linspace(0, 1, ramp)[:, None]
        clip[-ramp:] *= np.linspace(1, 0, ramp)[:, None]
        trimmed, lead, onset = trim_voice(clip)
        # The mixer adds a short room tail. Leave that in the speech slot too.
        mixed_duration = len(trimmed) / SR + .18 - onset
        if line["at"] + mixed_duration > line["until"]:
            raise ValueError(f"{line['id']} does not fit its speech slot")
        path = out / (line["id"] + ".wav")
        write_wav(path, clip)
        audible_source = start + lead + onset
        words = [word for word in response.get("original_words", [])
                 if first - .001 <= float(word["start_time"]) <= last]
        stems.append({**line, "path": str(path.resolve()),
                      "source_start": start, "source_end": end,
                      "source_audible_onset": audible_source,
                      "duration": len(clip) / SR,
                      "mixed_duration": mixed_duration,
                      "words": [{"text": w["text"],
                                 "start": round(line["at"] + float(w["start_time"]) - audible_source, 5),
                                 "end": round(line["at"] + float(w["end_time"]) - audible_source, 5)}
                                for w in words],
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        cursor = end_index + 2
    manifest = {"voice": narration["voice"], "requested_model": "server default",
                "returned_model": response.get("model"), "pitch": "unchanged", "speed": "unchanged",
                "original_take_duration": total,
                "original_take_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "timing_coordinate_system": "absolute seconds in the 140-second show soundtrack",
                "narration": stems}
    (out / "voice_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return stems


def prepare_music(source, output, duration):
    """Keep the licensed score continuous, with a deliberate theatrical dip."""
    original = decode(source)
    music = np.zeros((round(duration * SR), 2), dtype=np.float32)
    count = min(len(music), len(original))
    music[:count] = original[:count]
    # A gentle drop during the pretend crash; no record scratch or hard cut.
    times = np.arange(len(music)) / SR
    envelope = np.interp(times, [0, 71.8, 72.8, 77.2, 79.8, 81.4, duration],
                         [1, 1, .25, .035, .035, 1, 1])
    music *= envelope[:, None]
    # A gentle descending bell stumble marks the pretend freeze, followed by
    # rising, original bell sparkles for the magical recovery. No alarm sound.
    for at, frequency in [(73.0, 783.991), (73.5, 659.255), (74.0, 523.251),
                          (80.0, 659.255), (80.4, 783.991), (80.8, 987.767), (81.2, 1318.51)]:
        t = np.arange(round(1.6 * SR)) / SR
        bell = (.045 * np.sin(2 * np.pi * frequency * t) * np.exp(-3.5 * t)
                + .014 * np.sin(2 * np.pi * frequency * 2.76 * t) * np.exp(-6 * t))
        bell *= np.minimum(1, t / .006)
        start = round(at * SR)
        music[start:start + len(bell)] += bell[:, None]
    write_wav(output, music)


def mix(config, output_name):
    out = Path(config["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "mix_config.json"
    cfg_path.write_text(json.dumps(config, indent=2) + "\n")
    subprocess.run([sys.executable, str(ROOT / "tools/build_christmas_soundtrack.py"), str(cfg_path)],
                   check=True, stdout=subprocess.DEVNULL)
    destination = out / output_name
    destination.write_bytes((out / "christmas_show_master.mp3").read_bytes())
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--music", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    narration = json.loads((ROOT / "tools/house_chase_narration.json").read_text())
    response = json.loads(args.response.read_text())
    stems = split_take(response, args.voice, narration, out / "voice")
    plan = json.loads((ROOT / "custom_components/twinkly_area_state/christmas_show_house_chase.json").read_text())
    music = out / "show_music_prepared.wav"
    prepare_music(args.music, music, plan["soundtrack"]["duration"])
    credit = ("We Wish you a Merry Christmas — Kevin MacLeod (incompetech.com), CC BY 4.0. "
              "Adapted with British Santa narration, theatrical volume dip, original bell sparkles, "
              "ducking and mastering. AI-generated voice: HeyGen Willoughby.")
    config = {"music_path": str(music), "duration": 140,
              "output_dir": str(out / "show_mix"), "narration": stems,
              "voice": narration["voice"], "music_gain_db": -3.5,
              "voice_lufs": -18, "duck_db": 14, "master_lufs": -18,
              "master_lra": 14, "credit": credit}
    show = mix(config, plan["soundtrack"]["file"])
    (out / show.name).write_bytes(show.read_bytes())
    # The same measured words drive the standalone test; there is no second
    # generation, arbitrary audio speed-up, or changed numeral timing.
    countdown = [dict(entry, at=3 + i, until=3.92 + i)
                 for i, entry in enumerate(stems[11:16])]
    quiet = out / "countdown_bed.wav"
    write_wav(quiet, np.zeros((10 * SR, 2), dtype=np.float32))
    # The generic mixer's long-show preview is inapplicable to a ten-second
    # track, so build this fixed countdown using its same trim/gain convention.
    count_audio = np.zeros((10 * SR, 2), dtype=np.float32)
    from build_christmas_soundtrack import loudness
    for entry in countdown:
        voice, _, onset = trim_voice(decode(entry["path"]))
        measured = loudness(entry["path"])
        gain = min(-18 - float(measured["input_i"]), -3 - float(measured["input_tp"]))
        place = round((entry["at"] - onset) * SR)
        count_audio[place:place + len(voice)] += voice * 10 ** (gain / 20)
    count_wav = out / "landing_countdown_british_v1.wav"
    write_wav(count_wav, count_audio)
    count_mp3 = out / "landing_countdown_british_v1.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(count_wav),
                    "-codec:a", "libmp3lame", "-b:a", "320k", "-ar", str(SR),
                    "-metadata", "title=British Santa Landing Countdown", str(count_mp3)], check=True)
    qc = loudness(count_mp3)
    if float(qc["input_tp"]) > -1.5:
        raise ValueError("Countdown exceeds delivery true-peak ceiling")
    (out / "countdown_manifest.json").write_text(json.dumps({
        "file": count_mp3.name, "duration": 10, "voice": narration["voice"],
        "numeral_onsets": [3, 4, 5, 6, 7], "digits": [5, 4, 3, 2, 1],
        "source_stems": [entry["id"] for entry in countdown],
        "pitch": "unchanged", "speed": "unchanged",
        "integrated_lufs": float(qc["input_i"]), "true_peak_dbtp": float(qc["input_tp"]),
        "sha256": hashlib.sha256(count_mp3.read_bytes()).hexdigest(),
    }, indent=2) + "\n")
    print(json.dumps({"show": str(out / show.name), "countdown": str(count_mp3),
                      "voice_stems": len(stems), "show_duration": 140, "countdown_duration": 10}, indent=2))


if __name__ == "__main__":
    main()
