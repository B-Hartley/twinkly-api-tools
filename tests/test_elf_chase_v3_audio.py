"""Word-tail protection and refusal to publish an incomplete V3 soundtrack."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Audio authoring checks require optional numpy")
class ElfChaseV3AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            spec = importlib.util.spec_from_file_location("v3_audio_authoring", ROOT / "tools/build_elf_chase_v3_audio.py")
            cls.audio = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.audio)
        finally:
            sys.path.pop(0)

    def test_quiet_final_consonant_and_complete_original_tail_survive(self):
        import numpy as np
        sr = self.audio.SR
        original = np.zeros((sr * 2, 2), dtype=np.float32)
        original[sr // 4:sr] = .12
        # A separated, very quiet word ending must not be removed by a gate.
        original[round(1.3 * sr):round(1.5 * sr)] = 1e-5
        retained, lead, onset, _ = self.audio.retained_voice(original)
        self.assertGreater(lead, 0)
        self.assertLessEqual(onset, .05)
        self.assertEqual(len(retained) + round(lead * sr), len(original))
        np.testing.assert_array_equal(retained, original[round(lead * sr):])
        self.assertTrue(np.any(retained[-round(.7 * sr):-round(.5 * sr)] != 0))

    def test_missing_phrase_fails_before_any_soundtrack_or_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "deliverable"
            plan = {"narration": [{"id": "missing", "at": 1, "until": 3, "text": "Complete words."}]}
            with self.assertRaisesRegex(ValueError, "speech is incomplete"):
                self.audio.build(plan, root / "music.mp3", root / "voice", root / "responses", out)
            self.assertFalse(out.exists())

    def test_silent_or_truncated_input_cannot_be_called_speech(self):
        import numpy as np
        with self.assertRaisesRegex(ValueError, "silent"):
            self.audio.retained_voice(np.zeros((4800, 2), dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "samples"):
            self.audio.retained_voice(np.zeros((0, 2), dtype=np.float32))

    def test_word_timestamp_protects_a_late_quiet_consonant_from_slot_overrun(self):
        import numpy as np
        sr = self.audio.SR
        original = np.zeros((sr * 2, 2), dtype=np.float32)
        original[round(.2 * sr):round(.5 * sr)] = .12
        original[round(1.25 * sr):round(1.4 * sr)] = 1e-5
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "magic.wav").write_bytes(b"source fixture")
            (root / "magic.json").write_text(json.dumps({
                "original_words": [{"text": "Magic", "start_time": .2, "end_time": 1.4}],
            }))
            plan = {"narration": [{"id": "magic", "text": "Magic.", "at": .5, "until": 1.5}]}
            with patch.object(self.audio, "decode", return_value=original):
                with self.assertRaisesRegex(ValueError, "extend the score"):
                    self.audio.preflight(plan, root, root)

    def test_nonquiet_source_tail_cannot_overlap_the_next_phrase(self):
        import numpy as np
        sr = self.audio.SR
        voice = np.zeros((sr * 2, 2), dtype=np.float32)
        voice[round(1.7 * sr)] = .01
        before = ({"id": "before", "at": 0}, voice, 0,
                  {"conservative_speech_end_in_show": .8})
        after = ({"id": "after", "at": 1.5}, np.zeros((sr, 2)), .05, {})
        with self.assertRaisesRegex(ValueError, "tail overlaps"):
            self.audio.validate_tail_overlaps([before, after])
        voice[:] = 1e-7
        self.audio.validate_tail_overlaps([before, after])

    @unittest.skipUnless(shutil.which("ffmpeg"), "Float WAV check requires ffmpeg")
    def test_premaster_float_wav_preserves_peaks_above_full_scale(self):
        import numpy as np
        samples = np.tile(np.array([[1.25, -1.25]], dtype=np.float32), (480, 1))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "premaster.wav"
            self.audio.write_float_wav(path, samples)
            decoded = self.audio.decode(path)
            np.testing.assert_array_equal(decoded, samples)

    def fal_alignment(self):
        return {"timestamps": [
            {"characters": list("Hi, "),
             "character_start_times_seconds": [0, .1, .2, .3],
             "character_end_times_seconds": [.1, .2, .3, .4]},
            {"characters": list("Santa."),
             "character_start_times_seconds": [1, 1.1, 1.2, 1.3, 1.4, 1.5],
             "character_end_times_seconds": [1.1, 1.2, 1.3, 1.4, 1.5, 1.75]},
        ]}

    def test_fal_chunks_keep_absolute_times_and_exclude_punctuation_from_word_ends(self):
        result = self.audio.normalize_response(self.fal_alignment())
        self.assertEqual("".join(char["text"] for char in result["original_characters"]), "Hi, Santa.")
        self.assertEqual(result["original_words"], [
            {"text": "Hi", "start_time": 0, "end_time": .2},
            {"text": "Santa", "start_time": 1, "end_time": 1.5},
        ])
        self.assertEqual(result["original_characters"][-1]["end_time"], 1.75)

    def test_fal_alignment_rejects_missing_or_mismatched_arrays(self):
        response = self.fal_alignment()
        del response["timestamps"][0]["character_end_times_seconds"]
        with self.assertRaisesRegex(ValueError, "missing"):
            self.audio.normalize_response(response)
        response = self.fal_alignment()
        response["timestamps"][0]["character_start_times_seconds"].pop()
        with self.assertRaisesRegex(ValueError, "mismatched"):
            self.audio.normalize_response(response)

    def test_fal_alignment_rejects_reset_chunk_clocks_and_nonfinite_times(self):
        response = self.fal_alignment()
        response["timestamps"][1]["character_start_times_seconds"][0] = 0
        with self.assertRaisesRegex(ValueError, "absolute phrase clock"):
            self.audio.normalize_response(response)
        response = self.fal_alignment()
        response["timestamps"][0]["character_end_times_seconds"][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            self.audio.normalize_response(response)

    def test_fal_submission_text_and_selected_voice_must_match(self):
        request = {"endpoint_id": "elevenlabs/tts/eleven-v4",
                   "input": {"text": "Hi, Santa.", "voice": "selected", "timestamps": True}}
        result = self.audio.normalize_response(self.fal_alignment(), request)
        plan = {"voice": {"model": request["endpoint_id"], "voice_id": "selected"}}
        self.audio.validate_voice_identity(plan, result)
        plan["voice"]["voice_id"] = "another-voice"
        with self.assertRaisesRegex(ValueError, "selected V3 voice"):
            self.audio.validate_voice_identity(plan, result)
        request["input"]["text"] = "Different words."
        with self.assertRaisesRegex(ValueError, "submitted text"):
            self.audio.normalize_response(self.fal_alignment(), request)

    def test_legacy_heygen_word_alignment_remains_supported(self):
        response = {"original_words": [{"text": "Magic", "start_time": .2, "end_time": .8}]}
        self.assertEqual(self.audio.normalize_response(response), response)


if __name__ == "__main__":
    unittest.main()
