#!/usr/bin/env python3
"""Mix V3 from complete independent speech renders, never cut a word to fit.

Requires numpy and ffmpeg. This tool is offline: it makes no speech requests
and cannot play Sonos or operate lights. Each narration ID needs a WAV in
--voice-dir and its original HeyGen response JSON in --responses. No final
soundtrack is written unless every phrase exists, matches its text, and fits.
Use --music-only to prepare the instrumental source while speech is pending.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

import numpy as np

from build_christmas_soundtrack import SR, decode, loudness, run

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "custom_components/twinkly_area_state/christmas_show_elf_chase_v3.json"
CREDIT = (
    "We Wish you a Merry Christmas — Kevin MacLeod (incompetech.com), CC BY 4.0. "
    "Adapted with an instrumental repeat, crossfade, original bell accents, "
    "Santa narration, ducking and mastering. AI-generated voice: HeyGen Arthur Blackwood."
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_float_wav(path, samples):
    """Keep summed peaks above full scale intact until mastering applies gain."""
    if not np.all(np.isfinite(samples)):
        raise ValueError("Audio contains non-finite samples")
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ac", str(samples.shape[1]),
         "-ar", str(SR), "-i", "pipe:0", "-c:a", "pcm_f32le", str(path)],
        input=samples.astype("<f4").tobytes(), capture_output=True,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode())


def retained_voice(audio):
    """Remove dead lead-in only; retain EVERY sample through the source end.

    The old renderer's timestamp-plus-padding cut could precede a real word's
    ending. Independent phrases need no tail cut. Quiet consonants and breaths
    therefore survive even when a level detector would classify them as quiet.
    """
    if audio.ndim != 2 or not len(audio):
        raise ValueError("Speech must contain stereo samples")
    block = 240  # Five milliseconds at 48 kHz.
    mono = np.max(np.abs(audio), axis=1)
    padded = np.pad(mono, (0, (-len(mono)) % block))
    rms = np.sqrt(np.mean(padded.reshape(-1, block) ** 2, axis=1))
    threshold = max(10 ** (-62 / 20), float(rms.max()) * 10 ** (-43 / 20))
    active = np.flatnonzero(rms > threshold)
    if not len(active):
        raise ValueError("Speech render is silent")
    first = int(active[0]) * block
    start = max(0, first - round(.05 * SR))
    last = min(len(audio), (int(active[-1]) + 1) * block)
    # No trailing gate or fade: the generated original's ending stays intact.
    return audio[start:].copy(), start / SR, (first - start) / SR, (last - start) / SR


def normalized_words(text):
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


def preflight(plan, voice_dir, responses):
    missing = []
    for line in plan["narration"]:
        for path in (voice_dir / (line["id"] + ".wav"), responses / (line["id"] + ".json")):
            if not path.is_file():
                missing.append(str(path))
    if missing:
        raise ValueError("V3 speech is incomplete; no soundtrack will be created. Missing:\n" + "\n".join(missing))
    prepared = []
    for line in plan["narration"]:
        source = voice_dir / (line["id"] + ".wav")
        response_path = responses / (line["id"] + ".json")
        response = json.loads(response_path.read_text())
        characters = response.get("original_characters")
        if characters is not None:
            returned = "".join(char["text"] for char in characters)
            if returned != line["text"]:
                raise ValueError(f"{line['id']}: returned transcript differs from the score")
        else:
            returned = " ".join(word["text"] for word in response.get("original_words", []))
            if normalized_words(returned) != normalized_words(line["text"]):
                raise ValueError(f"{line['id']}: word transcript differs from the score")
        original = decode(source)
        voice, lead, onset, active_end = retained_voice(original)
        actual_end = line["at"] - onset + active_end
        source_words = response.get("original_words", [])
        timing = source_words or [char for char in characters or [] if char["text"].isalnum()]
        if not timing:
            raise ValueError(f"{line['id']}: source word/character timing is missing")
        last_word = max(float(item["end_time"]) for item in timing)
        if not math.isfinite(last_word) or not 0 < last_word <= len(original) / SR + .01:
            raise ValueError(f"{line['id']}: source speech timing is outside the original render")
        word_end = line["at"] - onset - lead + last_word
        speech_end = max(actual_end, word_end)
        if speech_end + .10 > line["until"]:
            raise ValueError(f"{line['id']}: complete speech plus safety gap ends at {speech_end + .10:.3f}s, "
                             f"after slot {line['until']}; extend the score instead of clipping or accelerating it")
        edge = original[-round(.02 * SR):]
        edge_rms = float(np.sqrt(np.mean(edge ** 2)))
        if edge_rms > 10 ** (-48 / 20):
            raise ValueError(f"{line['id']}: original render ends with non-quiet audio; review before mixing")
        prepared.append((line, voice, onset, {
            "source_file": source.name,
            "source_sha256": digest(source),
            "response_sha256": digest(response_path),
            "source_duration": len(original) / SR,
            "removed_leading_seconds": lead,
            "removed_trailing_seconds": 0,
            "audible_onset_in_retained_clip": onset,
            "last_detected_audio_in_show": actual_end,
            "last_timed_word_in_show": word_end,
            "conservative_speech_end_in_show": speech_end,
            "source_tail_rms_dbfs": 20 * math.log10(max(edge_rms, 1e-10)),
            "words": [{"text": word["text"],
                       "start": line["at"] - onset - lead + float(word["start_time"]),
                       "end": line["at"] - onset - lead + float(word["end_time"])}
                      for word in source_words],
        }))
    validate_tail_overlaps(prepared)
    return prepared


def validate_tail_overlaps(prepared):
    """Only very quiet original tails may overlap a following phrase's lead-in.

    RMS detection alone can miss a low-level consonant. The word-time deadline
    is checked separately, and an overlapping source tail must also be below
    -60 dBFS RMS and -50 dBFS peak. No overlapping samples are discarded.
    """
    for previous, current in zip(prepared, prepared[1:]):
        before, voice, onset, provenance = previous
        after, _, after_onset, _ = current
        next_start = after["at"] - after_onset
        start = before["at"] - onset
        overlap_from = max(0, round((next_start - start) * SR))
        tail = voice[overlap_from:]
        if len(tail):
            rms = float(np.sqrt(np.mean(tail ** 2)))
            peak = float(np.max(np.abs(tail)))
            if (provenance["conservative_speech_end_in_show"] + .10 > after["at"]
                    or rms > 10 ** (-60 / 20) or peak > 10 ** (-50 / 20)):
                raise ValueError(f"{before['id']}: non-quiet speech tail overlaps {after['id']}; extend the score")


def extend_music(original, duration):
    """Add exactly 72 seconds without altering pitch, tempo or the final cadence.

    The orchestra continues to 124.75s, crossfades for .75s into its earlier
    52s passage, then plays the source through its natural ending. This gives
    the longer chase its own instrumental repeat while retaining the opening.
    """
    cut = round(124.75 * SR)
    restart = round(52 * SR)
    overlap = round(.75 * SR)
    if len(original) <= cut + SR:
        raise ValueError("The credited full music source is too short for the V3 arrangement")
    head = original[:cut].copy()
    rest = original[restart:]
    theta = np.linspace(0, np.pi / 2, overlap)
    head[-overlap:] = head[-overlap:] * np.cos(theta)[:, None] + rest[:overlap] * np.sin(theta)[:, None]
    arranged = np.concatenate([head, rest[overlap:]])
    result = np.zeros((round(duration * SR), 2), dtype=np.float32)
    if len(arranged) > len(result):
        raise ValueError("Music arrangement would lose its ending")
    result[:len(arranged)] = arranged
    # A theatrical hush makes the hide-and-seek beat legible, then a warm lift.
    times = np.arange(len(result)) / SR
    envelope = np.interp(times, [0, 132, 134, 137.5, 141, 143.5, duration],
                         [1, 1, .22, .22, .48, 1, 1])
    result *= envelope[:, None]
    for at, frequency in [(138, 659.255), (139.3, 783.991), (140.6, 987.767), (142, 1318.51)]:
        t = np.arange(round(1.6 * SR)) / SR
        bell = .035 * np.sin(2 * np.pi * frequency * t) * np.exp(-3.5 * t)
        bell += .010 * np.sin(2 * np.pi * frequency * 2.76 * t) * np.exp(-6 * t)
        bell *= np.minimum(1, t / .006)
        start = round(at * SR)
        result[start:start + len(bell)] += bell[:, None]
    return result


def prepare_music(source, output, duration):
    original = decode(source)
    music = extend_music(original, duration)
    write_float_wav(output, music)
    manifest = {
        "source_file": source.name, "source_sha256": digest(source),
        "source_duration": len(original) / SR, "duration": duration,
        "inserted_seconds": 72, "crossfade_seconds": .75,
        "source_before_join_ends_at": 124.75, "source_after_join_starts_at": 52,
        "source_pitch": "unchanged", "source_speed": "unchanged",
        "prepared_sample_format": "32-bit float; peaks retained until mastering",
        "prepared_sha256": digest(output), "credit": CREDIT,
    }
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    return music, manifest


def build(plan, music_source, voice_dir, responses, output):
    prepared = preflight(plan, voice_dir, responses)
    output.mkdir(parents=True, exist_ok=True)
    duration = plan["soundtrack"]["duration"]
    track, music_manifest = prepare_music(music_source, output / "v3_instrumental_source.wav", duration)
    track *= 10 ** (-3.5 / 20)
    voices = np.zeros_like(track)
    envelope = np.ones(len(track), dtype=np.float32)
    spans = []
    for line, retained, onset, provenance in prepared:
        dry = output / (line["id"] + "_retained.wav")
        write_float_wav(dry, retained)
        measured = loudness(dry)
        gain = min(-18 - float(measured["input_i"]), -2.5 - float(measured["input_tp"]))
        filtered = run("ffmpeg", "-v", "error", "-i", str(dry), "-af",
                       f"highpass=f=50,volume={gain:.6f}dB", "-ac", "2", "-ar", str(SR), "-f", "f32le", "pipe:1").stdout
        voice = np.frombuffer(filtered, dtype="<f4").reshape(-1, 2).copy()
        place = round((line["at"] - onset) * SR)
        if place < 0 or place + len(voice) > len(voices):
            raise ValueError(f"{line['id']}: full phrase would exceed the master; extend the score")
        voices[place:place + len(voice)] += voice
        end = provenance["conservative_speech_end_in_show"] + .12
        local = track[place:place + len(voice)]
        vrms = float(np.sqrt(np.mean(voice ** 2)))
        mrms = float(np.sqrt(np.mean(local ** 2)))
        duck = min(14, max(3, 20 * math.log10(max(mrms, 1e-9) / max(vrms, 1e-9)) + 10))
        times = [max(0, line["at"] - .2), max(0, line["at"] - .035), end + .06, end + .55]
        levels = [1, 10 ** (-duck / 20), 10 ** (-duck / 20), 1]
        lo, hi = int(times[0] * SR), min(len(track), math.ceil(times[-1] * SR))
        envelope[lo:hi] = np.minimum(envelope[lo:hi], np.interp(np.arange(lo, hi) / SR, times, levels))
        spans.append({**line, **provenance, "gain_db": gain, "music_duck_db": duck,
                      "retained_end_in_show": (place + len(voice)) / SR})
    premaster = output / "v3_premaster.wav"
    premaster_samples = track * envelope[:, None] + voices
    premaster_peak = float(np.max(np.abs(premaster_samples)))
    write_float_wav(premaster, premaster_samples)
    measured = loudness(premaster)
    gain = min(-18 - float(measured["input_i"]), -2 - float(measured["input_tp"]))
    master = output / "christmas_show_elf_chase_v3.wav"
    run("ffmpeg", "-v", "error", "-y", "-i", str(premaster), "-af", f"volume={gain:.6f}dB",
        "-ar", str(SR), "-c:a", "pcm_s24le", str(master))
    mp3 = output / plan["soundtrack"]["file"]
    run("ffmpeg", "-v", "error", "-y", "-i", str(master), "-codec:a", "libmp3lame",
        "-b:a", "320k", "-ar", str(SR), "-metadata", "title=" + plan["name"],
        "-metadata", "comment=" + CREDIT, str(mp3))
    qc = loudness(mp3)
    if float(qc["input_tp"]) > -1.5:
        raise ValueError("Encoded MP3 exceeds the -1.5 dBTP delivery ceiling")
    manifest = {
        "voice": plan["voice"], "duration": duration, "sample_rate": SR, "channels": 2,
        "speech_pitch": "unchanged", "speech_speed": "unchanged", "speech_tail_trimming": "none",
        "timing_coordinate_system": "absolute seconds in the 212-second V3 soundtrack",
        "music": music_manifest, "narration": spans,
        "premaster_sample_format": "32-bit float; no fixed-point clipping before mastering",
        "premaster_sample_peak": premaster_peak,
        "master_gain_db": gain, "integrated_lufs": float(qc["input_i"]),
        "true_peak_dbtp": float(qc["input_tp"]), "loudness_range_lu": float(qc["input_lra"]),
        "wav_sha256": digest(master), "mp3_sha256": digest(mp3),
        "review_note": "Inspect/listen to every phrase and join before installing; timestamps do not prove pronunciation quality.",
    }
    (output / "production_manifest_v3.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return mp3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--music", type=Path, required=True)
    parser.add_argument("--voice-dir", type=Path)
    parser.add_argument("--responses", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--music-only", action="store_true")
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text())
    if args.music_only:
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / "v3_instrumental_source.wav"
        prepare_music(args.music, path, plan["soundtrack"]["duration"])
        print(f"Prepared instrumental SOURCE only, not a complete show soundtrack: {path}")
    else:
        if args.voice_dir is None or args.responses is None:
            parser.error("--voice-dir and --responses are required for a complete soundtrack")
        print(build(plan, args.music, args.voice_dir, args.responses, args.output))


if __name__ == "__main__":
    main()
