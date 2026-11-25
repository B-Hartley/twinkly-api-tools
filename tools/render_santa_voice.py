#!/usr/bin/env python3
"""Render the show's Santa narration with Kokoro's neural character voices.

Requires kokoro-onnx==0.4.9, soundfile, numpy and the Apache-2.0 Kokoro v1.0
ONNX model/voice weights. No remote service, speaker, or controller is called.
Use --model-dir to select the separately downloaded model files. The default
blend combines the Santa character with British George for clearer diction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    from kokoro_onnx import Kokoro

    args.output.mkdir(parents=True, exist_ok=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 3
    options.inter_op_num_threads = 1
    model = args.model_dir / 'kokoro-v1.0.onnx'
    voices = args.model_dir / 'voices-v1.0.bin'
    engine = Kokoro.from_session(
        ort.InferenceSession(str(model), options, providers=['CPUExecutionProvider']),
        str(voices),
    )
    style = 0.65 * engine.get_voice_style('am_santa') + 0.35 * engine.get_voice_style('bm_george')
    plan = json.loads(args.plan.read_text(encoding='utf-8'))
    entries = []
    for line in plan['narration']:
        speed = float(line.get('speed', 0.88))
        samples, rate = engine.create(line['text'], voice=style, speed=speed, lang='en-gb', trim=True)
        samples = np.asarray(samples, dtype=np.float32)
        samples -= np.mean(samples)
        audible = np.flatnonzero(np.abs(samples) > 0.0005)
        if not len(audible):
            raise ValueError(f"Silent speech: {line['id']}")
        pad = round(rate * 0.025)
        samples = samples[max(0, int(audible[0]) - pad):min(len(samples), int(audible[-1]) + pad + 1)]
        fade = round(rate * 0.003)
        samples[:fade] *= np.linspace(0, 1, fade)
        samples[-fade:] *= np.linspace(1, 0, fade)
        duration = len(samples) / rate
        if float(line['at']) + duration > float(line['until']):
            raise ValueError(f"{line['id']} ends at {line['at'] + duration:.3f}s, beyond {line['until']}s; shorten the script instead of rushing the voice")
        samples *= (10 ** (-6 / 20)) / max(float(np.max(np.abs(samples))), 1e-9)
        path = args.output / (line['id'] + '.wav')
        sf.write(path, samples, rate, subtype='PCM_24')
        entry = {**line, 'path': str(path.resolve()), 'duration': round(duration, 6),
                 'end': round(float(line['at']) + duration, 6), 'sample_rate': rate,
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        entries.append(entry)
        print(f"{line['id']}: {duration:.3f}s, ends {entry['end']:.3f}s", flush=True)
    result = {'voice': '65% Kokoro am_santa / 35% bm_george',
              'voice_type': 'Synthetic neural Santa-style character, not a human actor',
              'language': 'en-gb', 'model_license': 'Apache-2.0',
              'model_source': 'https://huggingface.co/hexgrad/Kokoro-82M',
              'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
              'narration': entries}
    (args.output / 'voice_manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
