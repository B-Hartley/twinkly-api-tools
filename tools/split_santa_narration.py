#!/usr/bin/env python3
"""Split a timestamped HeyGen narration take into the score's exact phrase stems.

The script uses the returned character timing, verifies the complete requested
text, and cuts only between phrases. No new speech request, pitch change or
time stretching is performed. Requires numpy and ffmpeg.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.response.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    characters = result["original_characters"]
    transcript = "".join(char["text"] for char in characters)
    expected = "\n\n".join(line["text"] for line in plan["narration"])
    if transcript != expected:
        raise ValueError("Returned character transcript differs from the requested script")
    # HeyGen's character records must genuinely address individual characters.
    if any(len(char["text"]) != 1 for char in characters):
        raise ValueError("Unexpected character alignment format")
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(args.audio), "-ac", "1", "-ar", "48000",
        "-f", "f32le", "pipe:1",
    ])
    audio = np.frombuffer(raw, dtype="<f4").copy()
    rate = 48000
    args.output.mkdir(parents=True, exist_ok=True)
    cursor = 0
    entries = []
    for index, line in enumerate(plan["narration"]):
        end_index = cursor + len(line["text"])
        selected = characters[cursor:end_index]
        spoken = [char for char in selected if char["text"].isalnum()]
        first = float(spoken[0]["start_time"])
        last = max(float(char["end_time"]) for char in spoken)
        following = (float(characters[end_index + 2]["start_time"])
                     if index + 1 < len(plan["narration"]) else len(audio) / rate)
        cut_start = max(0, first - .060)
        cut_end = min(following - .045, last + .160, len(audio) / rate)
        clip = audio[round(cut_start * rate):round(cut_end * rate)].copy()
        ramp = min(round(.003 * rate), len(clip) // 4)
        clip[:ramp] *= np.linspace(0, 1, ramp)
        clip[-ramp:] *= np.linspace(1, 0, ramp)
        if line["at"] + len(clip) / rate > line["until"]:
            raise ValueError(f"{line['id']} does not fit; adjust the score or script explicitly")
        path = args.output / (line["id"] + ".wav")
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ac", "1", "-ar", str(rate),
            "-i", "pipe:0", "-c:a", "pcm_s24le", str(path),
        ], input=clip.astype("<f4").tobytes(), check=True)
        entries.append({**line, "path": str(path.resolve()), "sample_rate": rate,
                        "duration": round(len(clip) / rate, 6), "source_start": cut_start,
                        "source_end": cut_end, "first_word_at_in_source": first,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        print(line["id"], f"{len(clip) / rate:.3f}s", flush=True)
        cursor = end_index + 2
    manifest = {"voice": "HeyGen Santa - Public", "voice_id": "83f8d11946b24588857a491e1841c667",
                "voice_type": "Synthetic Santa character", "provider": "HeyGen",
                "original_take_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
                "narration": entries}
    (args.output / "voice_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
