"""Protect V2's measured icicle word ending without changing other stems."""
from contextlib import ExitStack
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Audio authoring checks require optional numpy")
class HouseChaseAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            cls.audio = importlib.import_module("build_house_chase_audio")
            cls.repair = importlib.import_module("repair_house_chase_magic")
        finally:
            sys.path.pop(0)

    def setUp(self):
        import numpy as np
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.source = self.root / "original.wav"
        self.source.write_bytes(b"measured voice fixture")
        self.other_source = self.root / "another_take.wav"
        self.other_source.write_bytes(b"different voice fixture")
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.stack.enter_context(patch.object(self.audio, "MEASURED_V2_SOURCE_SHA256", self.source_hash))
        self.stack.enter_context(patch.object(self.repair, "EXPECTED_SOURCE", self.source_hash))

        sr = self.audio.SR
        self.samples = np.zeros((23 * sr, 2), dtype=np.float32)
        for start, end in [(17.0, 18.5), (19.2, 21.1), (21.6, 22.0)]:
            self.samples[round(start * sr):round(end * sr)] = .1
        # This final syllable occurs after the provider-derived 20.967s cut.
        self.samples[round(21.0 * sr):round(21.08 * sr)] = .03
        self.stack.enter_context(patch.object(self.audio, "decode", return_value=self.samples))
        self.written = {}

        def capture_wav(path, samples):
            self.written[path] = samples.copy()
            path.write_bytes(samples.astype("<f4").tobytes())

        self.stack.enter_context(patch.object(self.audio, "write_wav", side_effect=capture_wav))
        narration = json.loads((ROOT / "tools/house_chase_narration.json").read_text())
        self.narration = {"voice": narration["voice"], "narration": narration["narration"][3:6]}

    def response(self, following=21.361):
        characters = []
        boundaries = [(16.839, 18.588), (19.015, 20.807), (following, 22.1)]
        for line, (first, last) in zip(self.narration["narration"], boundaries):
            text = line["text"]
            last_spoken = max(i for i, char in enumerate(text) if char.isalnum())
            step = (last - first) / (last_spoken + 1)
            for i, char in enumerate(text):
                characters.append({"text": char, "start_time": first + i * step,
                                   "end_time": first + (i + 1) * step})
            characters.extend({"text": "\n", "start_time": last, "end_time": last}
                              for _ in range(2))
        return {"original_characters": characters[:-2]}

    def build(self, name="voice", source=None, response=None):
        return self.audio.split_take(response or self.response(), source or self.source,
                                     self.narration, self.root / name)

    def test_standard_build_retains_late_word_tail_and_writes_matching_manifest(self):
        import numpy as np
        repaired = self.build()
        unmeasured = self.build("other", source=self.other_source)
        entry, old_entry = repaired[1], unmeasured[1]
        samples = self.written[Path(entry["path"])]
        tail_offset = round((21.0 - entry["source_start"]) * self.audio.SR)
        np.testing.assert_array_equal(
            samples[tail_offset:tail_offset + 100],
            self.samples[21 * self.audio.SR:21 * self.audio.SR + 100],
        )
        self.assertGreater(tail_offset, len(self.written[Path(old_entry["path"])]))
        self.assertAlmostEqual(entry["duration"], 2.345)
        self.assertAlmostEqual(entry["source_end"], 21.3)
        self.assertAlmostEqual(old_entry["source_end"], 20.967)
        self.assertGreater(entry["mixed_duration"], old_entry["mixed_duration"] + .1)
        self.assertEqual(entry["source_audible_onset"], old_entry["source_audible_onset"])
        self.assertLess(entry["at"] + entry["mixed_duration"], entry["until"])
        self.assertNotIn("source_end_before_repair", old_entry)
        manifest = json.loads((self.root / "voice/voice_manifest.json").read_text())
        self.assertEqual(manifest["narration"], repaired)
        self.assertEqual(manifest["original_take_sha256"], self.source_hash)
        for written in repaired:
            self.assertEqual(written["sha256"], hashlib.sha256(Path(written["path"]).read_bytes()).hexdigest())
        for index in (0, 2):
            np.testing.assert_array_equal(self.written[Path(repaired[index]["path"])],
                                          self.written[Path(unmeasured[index]["path"])])
            self.assertEqual({k: v for k, v in repaired[index].items() if k != "path"},
                             {k: v for k, v in unmeasured[index].items() if k != "path"})

    def test_repaired_word_must_fit_even_when_old_truncated_clip_would_fit(self):
        self.narration["narration"][1]["until"] = 42.0
        self.build("old_cut", source=self.other_source)
        with self.assertRaisesRegex(ValueError, "05_icicles does not fit"):
            self.build()
        self.assertFalse((self.root / "voice/05_icicles.wav").exists())
        self.assertFalse((self.root / "voice/voice_manifest.json").exists())

    def test_measured_tail_cannot_include_the_next_phrase(self):
        with self.assertRaisesRegex(ValueError, "next phrase boundary"):
            self.build(response=self.response(following=21.32))
        self.assertFalse((self.root / "voice/05_icicles.wav").exists())

    def test_source_identity_does_not_allow_a_different_phrase_or_alignment(self):
        self.narration["narration"][1]["text"] = "Icicles, a touch of winter wonder."
        with self.assertRaisesRegex(ValueError, "original phrase text and alignment"):
            self.build()
        self.narration["narration"][1]["text"] = "Icicles, a touch of winter magic."
        response = self.response()
        first_icicle = len(self.narration["narration"][0]["text"]) + 2
        response["original_characters"][first_icicle]["start_time"] = 18.5
        with self.assertRaisesRegex(ValueError, "original phrase text and alignment"):
            self.build(response=response)

    def test_repair_cli_uses_the_same_stems_and_updated_manifest_as_normal_build(self):
        tools = self.root / "repository/tools"
        tools.mkdir(parents=True)
        (tools / "house_chase_narration.json").write_text(json.dumps(self.narration))
        response = self.root / "response.json"
        response.write_text(json.dumps(self.response()))
        output = self.root / "repaired"
        captured = {}

        def capture_mix(config, name):
            captured.update(config)
            path = self.root / name
            path.write_bytes(b"rendered soundtrack fixture")
            return path

        argv = ["repair_house_chase_magic.py", "--voice", str(self.source),
                "--response", str(response), "--music", str(self.root / "music.mp3"),
                "--output", str(output)]
        with patch.object(sys, "argv", argv), patch.object(self.repair, "ROOT", tools.parent), \
                patch.object(self.repair, "prepare_music"), \
                patch.object(self.repair, "mix", side_effect=capture_mix), patch("builtins.print"):
            self.repair.main()
        voice_manifest = json.loads((output / "voice/voice_manifest.json").read_text())
        repair_manifest = json.loads((output / "magic_repair_manifest.json").read_text())
        self.assertEqual(captured["narration"], voice_manifest["narration"])
        self.assertEqual(captured["duration"], 140)
        entry = captured["narration"][1]
        self.assertAlmostEqual(repair_manifest["added_source_tail_seconds"], .333)
        self.assertEqual(repair_manifest["phrase_mixed_duration_seconds"], entry["mixed_duration"])
        self.assertEqual(entry["sha256"], hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest())
        self.assertGreater(entry["mixed_duration"], 2.0)


if __name__ == "__main__":
    unittest.main()
