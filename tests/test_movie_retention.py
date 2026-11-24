"""Offline tests for non-destructive movie and playlist round-trip checks."""
import copy
import unittest
from unittest.mock import patch

import movie_retention as movies
import twinkly_inspector as inspector


def record(ip, names=("Candy Cane", "RainbowSwing"), playlist=None):
    listing = [{"unique_id": "movie-uuid-" + name, "name": name,
                "frames_number": 20, "leds_per_frame": 420,
                "fps": 12, "descriptor_type": "rgbw", "id": index}
               for index, name in enumerate(names)]
    return {"name": ip, "ip": ip, "movies": listing, "responses": {
        "playlist": {"ok": True, "data": {"entries": playlist if playlist is not None else []}}}}


class MovieRetentionTests(unittest.TestCase):
    def test_current_movie_endpoint_matches_verified_iobroker_implementation(self):
        self.assertEqual(inspector.ENDPOINTS["movies_current"], "/xled/v1/movies/current")
        self.assertEqual(movies.CURRENT, "/xled/v1/movies/current")

    def test_all_original_movies_and_playlist_preserved(self):
        before = [record("192.168.21.90"), record("192.168.21.93")]
        after = copy.deepcopy(before)
        after[0]["movies"][0]["id"] = 7  # Device-local slot ID can move.
        report = movies.compare(before, after)
        self.assertTrue(all(r["preserved"] for r in report))

    def test_missing_movie_uuid_is_not_silently_accepted(self):
        before = [record("192.168.21.90")]
        after = [record("192.168.21.90", ("Candy Cane",))]
        report = movies.compare(before, after)
        self.assertFalse(report[0]["preserved"])
        self.assertEqual(report[0]["missing"][0]["name"], "RainbowSwing")

    def test_changed_frame_metadata_is_detected(self):
        before = [record("192.168.21.90")]
        after = copy.deepcopy(before)
        after[0]["movies"][0]["frames_number"] = 10
        report = movies.compare(before, after)
        self.assertFalse(report[0]["preserved"])
        self.assertEqual(report[0]["modified"][0]["changed_fields"], ["frames_number"])

    def test_playlist_change_detected(self):
        before = [record("192.168.21.90", playlist=[{"id": 1, "name": "A"}])]
        after = [record("192.168.21.90", playlist=[])]
        report = movies.compare(before, after)
        self.assertFalse(report[0]["preserved"])
        self.assertFalse(report[0]["playlist_preserved"])

    def test_authenticated_read_only_captures_current_and_movie_list(self):
        member = {"ip": "192.168.21.90", "name": "Office Left"}
        def fake_request(ip, endpoint, *, token=None, timeout=None):
            if endpoint == movies.MOVIES:
                return {"ok": True, "data": {"movies": [{
                    "id": 2, "unique_id": "abc", "name": "RainbowSwing",
                    "frames_number": 20}]}}
            return {"ok": True, "data": {"entries": []}}
        with patch("movie_retention.authenticate", return_value=("token", {})), \
             patch("movie_retention.request", side_effect=fake_request) as req:
            snapshot = movies.read_member(member, 5)
        self.assertEqual(len(snapshot["movies"]), 1)
        self.assertEqual(req.call_count, 5)
        self.assertTrue(all(call.kwargs.get("token") == "token" for call in req.call_args_list))
        self.assertFalse(snapshot["raw_movie_frames_captured"])

    def test_missing_movie_uuid_blocks_without_claiming_restoration(self):
        with patch("movie_retention.authenticate", return_value=("token", {})), \
             patch("movie_retention.request", return_value={
                 "ok": True, "data": {"movies": [{"id": 2, "name": "No UUID"}]}}):
            with self.assertRaisesRegex(RuntimeError, "unique_id absent"):
                movies.read_member({"ip": "192.168.21.90", "name": "Left"}, 5)


if __name__ == "__main__":
    unittest.main()
