import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("front_pacing", Path(__file__).resolve().parents[1] / "Front_movies_V3" / "upload_movies.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

class PacingTests(unittest.TestCase):
    def test_fast_reads_and_auth(self):
        p = u.Pacing(slow=False)
        for endpoint in ('gestalt', 'login', 'verify', 'application/group', 'led/mode', 'movies'):
            self.assertEqual(p.gap('192.168.1.2', endpoint), .25)

    def test_sticky_independent_fallback(self):
        p = u.Pacing(slow=False)
        for expected in (.75, 2., 2.):
            p.backoff('192.168.1.2')
            self.assertEqual(p.gap('192.168.1.2', 'movies'), expected)
            self.assertEqual(p.gap('192.168.1.3', 'movies'), .25)

    def test_write_guards_both_sides(self):
        p = u.Pacing(slow=False)
        for endpoint in ('movies/new', 'movies/full'):
            self.assertEqual(p.gap('192.168.1.2', endpoint), 2.)
            p.finished(endpoint)
            self.assertEqual(p.gap('192.168.1.3', 'gestalt'), 2.)
            p.finished('gestalt')
            self.assertEqual(p.gap('192.168.1.3', 'login'), .25)

    def test_slow_and_import_default(self):
        self.assertEqual(u.PACING.gap('192.168.1.2', 'movies'), 2.)
        p = u.Pacing(slow=True)
        p.backoff('192.168.1.2')
        self.assertEqual(p.gap('192.168.1.2', 'movies'), 2.)

    def test_wait_from_completion(self):
        p = u.Pacing(slow=False)
        p.last_finished = 10.
        with patch.object(u.time, 'monotonic', return_value=10.125), patch.object(u.time, 'sleep') as sleep:
            p.wait('192.168.1.2', 'gestalt')
            sleep.assert_called_once_with(.125)

    def test_http401_and_timeout_trigger_fallback_without_write_retry(self):
        p = u.Pacing(slow=False)
        with patch.object(u, 'PACING', p), patch.object(u.time, 'sleep'):
            with patch.object(u.OPENER, 'open', side_effect=urllib.error.HTTPError('http://192.168.1.2', 401, 'Unauthorized', {}, None)) as request:
                with self.assertRaises(RuntimeError):
                    u.call('192.168.1.2', 'led/mode', 45)
                request.assert_called_once()
            self.assertEqual(p.gap('192.168.1.2', 'movies'), .75)
            with patch.object(u.OPENER, 'open', side_effect=TimeoutError('timed out')) as request:
                with self.assertRaises(RuntimeError):
                    u.call('192.168.1.2', 'movies/full', 45, payload=b'data')
                request.assert_called_once()
            self.assertEqual(p.levels['192.168.1.2'], 2)
            self.assertTrue(p.last_write)

    def test_read_retry_restarts_inspection(self):
        with patch.object(u, 'inspect_once', side_effect=[RuntimeError('401'), RuntimeError('timeout'), ('state', 'fresh-token')]) as inspect, patch.object(u.time, 'sleep') as sleep:
            self.assertEqual(u.inspect({'name':'test'}, 45), ('state', 'fresh-token'))
            self.assertEqual(inspect.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
