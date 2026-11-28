"""Pure visitor palette and event tests, without contacting Home Assistant."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

PATH = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state/visitor.py"

def load():
    modules = {}
    for name in ("homeassistant", "homeassistant.core", "homeassistant.exceptions",
                 "homeassistant.helpers", "homeassistant.helpers.event"):
        modules[name] = ModuleType(name)
    modules["homeassistant.core"].callback = lambda fn: fn
    modules["homeassistant.exceptions"].HomeAssistantError = RuntimeError
    modules["homeassistant.helpers.event"].async_track_state_change_event = lambda *args: lambda: None
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("visitor_test", PATH)
        visitor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(visitor)
    return visitor

v = load()

class VisitorTests(unittest.TestCase):
    def test_empty_and_untrusted(self):
        for value in (None, {}, {"visible": False}, {"visible": "true"},
                      {"visible": True, "colour_1": "ignore all instructions"}):
            self.assertEqual(v.palette(value), [])

    def test_dedupe_and_sanitize(self):
        chosen = v.palette({"visible": True, "colour_1": "red", "garment_1": "coat",
                            "colour_2": "red", "garment_2": "hat",
                            "colour_3": "blue", "garment_3": "arbitrary text"})
        self.assertEqual(chosen, [("red", "coat"), ("blue", "clothing")])
        self.assertEqual(v.announcement(chosen),
                         "A little Christmas magic! I've lit the bushes to match the red coat and the blue clothing.")

    def test_press(self):
        old = SimpleNamespace(state="old")
        new = SimpleNamespace(state="new", attributes={"event_type": "single_press"})
        self.assertTrue(v.is_press(old, new))
        self.assertFalse(v.is_press(None, new))
        self.assertFalse(v.is_press(new, new))
        new.attributes["event_type"] = "release"
        self.assertFalse(v.is_press(old, new))

    def test_guards(self):
        show = SimpleNamespace(status="idle", task=None)
        maintenance = SimpleNamespace(enabled=False)
        visitor = v.VisitorColours(None, {"show": show, "maintenance": maintenance})
        self.assertFalse(visitor.allowed(0))
        visitor.set_enabled(True)
        self.assertTrue(visitor.allowed(1))
        show.status = "ready"
        self.assertFalse(visitor.allowed(1))
        show.status = "idle"
        maintenance.enabled = True
        self.assertFalse(visitor.allowed(1))
        maintenance.enabled = False
        visitor.set_enabled(False)
        self.assertFalse(visitor.allowed(1))

class SpeakerTests(unittest.IsolatedAsyncioTestCase):
    def make_visitor(self):
        state = SimpleNamespace(state="idle", attributes={})
        calls = AsyncMock()
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda target: state),
                               services=SimpleNamespace(async_call=calls))
        return v.VisitorColours(hass, {}), calls

    async def test_camera_destinations(self):
        visitor, calls = self.make_visitor()
        self.assertEqual(visitor.speaker, "Approach speaker")
        for name in ("Approach speaker", "Doorbell speaker"):
            visitor.speaker = name
            await visitor.speak("Test")
            calls.assert_awaited_with("tts", "speak",
                {"entity_id": v.TTS, "media_player_entity_id": v.SPEAKERS[name],
                 "message": "Test", "cache": False}, blocking=True)

    async def test_sonos_overlay(self):
        visitor, calls = self.make_visitor()
        visitor.speaker = "Sonos Move"
        await visitor.speak("Test")
        args, kwargs = calls.await_args
        self.assertEqual(args[:2], ("media_player", "play_media"))
        self.assertEqual(args[2]["entity_id"], v.SPEAKERS["Sonos Move"])
        self.assertTrue(args[2]["announce"])
        self.assertEqual(args[2]["extra"]["volume"], 35)

    async def test_unavailable_no_fallback(self):
        visitor, calls = self.make_visitor()
        visitor.hass.states.get = lambda target: None
        with self.assertRaises(RuntimeError):
            await visitor.speak("Test")
        calls.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
