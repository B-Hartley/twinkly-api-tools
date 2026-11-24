"""Regression tests for complete Twinkly HTTP reads and grouped 3D capture.

Run with: python -m unittest tests/test_ha_capture_retries.py -v
Does not need Home Assistant or contact any controllers.
"""
import ast
import asyncio
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state" / "__init__.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))


def load_functions(*names):
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"asyncio": asyncio, "json": json, "MAX_RESPONSE": 8 * 1024 * 1024}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


functions = load_functions("parse_twinkly_json", "query", "_capture_live_3d")
query = functions["query"]
functions["_LOGGER"] = __import__("logging").getLogger(__name__)


class FakeContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.calls = 0

    async def read(self, count):
        self.calls += 1
        return self.chunks.pop(0) if self.chunks else b""


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, chunks):
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, attempts):
        self.attempts = list(attempts)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return FakeResponse(self.attempts.pop(0))


class CaptureRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_all_chunks_before_parsing(self):
        session = FakeSession([[b'{"coordinates":[', b'{"x":1}', b'],"code":1000}']])
        answer = await query(session, "127.0.0.1", "/layout")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["data"]["coordinates"], [{"x": 1}])
        self.assertEqual(session.calls, 1)

    async def test_retries_truncated_get_after_cooldown(self):
        session = FakeSession([
            [b'{"coordinates":['],
            [b'{"coordinates":[],"code":1000}'],
        ])
        slept = []
        async def sleep(seconds):
            slept.append(seconds)
        functions["asyncio"] = type("AsyncioShim", (), {
            "sleep": staticmethod(sleep),
            "TimeoutError": asyncio.TimeoutError,
        })
        try:
            answer = await query(session, "127.0.0.1", "/layout")
        finally:
            functions["asyncio"] = asyncio
        self.assertTrue(answer["ok"])
        self.assertEqual(session.calls, 2)
        self.assertEqual(slept, [1.0])

    async def test_does_not_retry_post(self):
        session = FakeSession([[b'{"broken":']])
        answer = await query(session, "127.0.0.1", "/login", method="POST")
        self.assertFalse(answer["ok"])
        self.assertEqual(session.calls, 1)

    async def test_preserves_final_raw_response_for_debugging(self):
        session = FakeSession([[b'{"coordinates":[']] * 3)
        debug = {}
        answer = await query(session, "127.0.0.1", "/layout", debug_capture=debug)
        self.assertFalse(answer["ok"])
        self.assertIn("3 attempt(s)", answer["error"])
        self.assertEqual(debug["attempt"], 3)
        self.assertEqual(debug["body"], b'{"coordinates":[')


class Grouped3DTests(unittest.TestCase):
    def test_grouped_controller_reports_ungroup_instruction(self):
        state = {"devices": [
            {"name": "Office Left", "responses": {
                "group": {"data": {"group": {"mode": "master"}}}
            }}
        ]}
        with self.assertRaisesRegex(ValueError, "ungroup") as ctx:
            functions["_capture_live_3d"](state)
        self.assertIn("Office Left", str(ctx.exception))
        self.assertIn("No maps were saved", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
