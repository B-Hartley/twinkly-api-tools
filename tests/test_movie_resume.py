import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("front_upload", Path(__file__).resolve().parents[1] / "Front_movies_V3" / "upload_movies.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.member = {"mac":"abc", "group_uid":"GROUP"}
        self.movie = {"slug":"test","name":"Test","unique_id":"ABC","frames":10}
        self.meta = {"name":"Test","unique_id":"ABC","frames_number":10}
        self.jobs = {("test","abc"):(self.meta,b"data")}
        self.key = u.journal_key(self.member,self.meta,b"data")

    def pending(self, entries, stage=None, resume=True, accept=False, free=100):
        return u.pending_movies(self.member,{"movies":entries,"max":10,"available_frames":free},
            [self.movie],self.jobs,{self.key:stage} if stage else {},resume,accept)

    def test_missing(self):
        self.assertEqual(self.pending([]),[self.movie])

    def test_verified_skipped_even_without_free_frames(self):
        self.assertEqual(self.pending([self.meta],"verified",free=0),[])

    def test_legacy_requires_explicit_acceptance(self):
        with self.assertRaises(ValueError):
            self.pending([self.meta])
        self.assertEqual(self.pending([self.meta],accept=True),[])

    def test_incomplete_binary_refused(self):
        for stage in ("attempt","created"):
            with self.assertRaises(ValueError):
                self.pending([self.meta],stage,accept=True)

    def test_conflict_refused(self):
        with self.assertRaises(ValueError):
            self.pending([{**self.meta,"frames_number":11}],accept=True)

    def test_duplicate_refused(self):
        with self.assertRaises(ValueError):
            self.pending([self.meta,self.meta],accept=True)

    def test_non_resume_refused(self):
        with self.assertRaises(ValueError):
            self.pending([self.meta],"verified",resume=False)

    def test_binary_ack_can_resume(self):
        self.assertEqual(self.pending([self.meta],"binary-ok"),[])

    def test_absent_after_attempt_can_resume(self):
        self.assertEqual(self.pending([],"attempt"),[self.movie])
