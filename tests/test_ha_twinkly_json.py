"""Test strict Twinkly JSON compatibility parsing without HA dependencies."""
import ast
import json
from pathlib import Path
import unittest


def load_parser():
    source = Path(__file__).resolve().parents[1] / "custom_components" / "twinkly_area_state" / "__init__.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_twinkly_json")
    module = ast.Module(body=[target], type_ignores=[])
    class DummyLogger:
        def debug(self, *args, **kwargs):
            pass
    namespace = {"json": json, "_LOGGER": DummyLogger()}
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["parse_twinkly_json"]


parse = load_parser()


class TestHATwinklyJSON(unittest.TestCase):
    def test_normal_layout(self):
        self.assertEqual(parse(b'{"code":1000,"coordinates":[{"x":1}]}'),
                         {"code": 1000, "coordinates": [{"x": 1}]})

    def test_known_connection_close_suffix(self):
        raw = b'{"code":1000,"coordinates":[{"x":1}]}\r\nConnection: close\r\n'
        self.assertEqual(parse(raw)["code"], 1000)

    def test_reject_incomplete_layout(self):
        with self.assertRaisesRegex(ValueError, "Malformed device JSON"):
            parse(b'{"code":1000,"coordinates":[{"x":1}')

    def test_reject_arbitrary_trailing_content(self):
        with self.assertRaisesRegex(ValueError, "unrecognised_suffix"):
            parse(b'{"code":1000}HTTP/1.1 200 OK')

    def test_never_accept_two_json_documents(self):
        with self.assertRaises(ValueError):
            parse(b'{"code":1000}{"code":1000}')

    def test_reject_mid_body_corruption(self):
        with self.assertRaisesRegex(ValueError, "Malformed device JSON"):
            parse(b'{"code":1000,"coords": [} nonsense')

    def test_no_raw_body_in_error_message(self):
        try:
            parse(b'{"password":"very-secret"} extraneous')
        except ValueError as exc:
            self.assertNotIn("very-secret", str(exc))
        else:
            self.fail("Malformed response unexpectedly accepted")


if __name__ == "__main__":
    unittest.main()
