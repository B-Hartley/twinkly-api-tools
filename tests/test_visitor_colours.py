"""Pure visitor palette and event tests, without contacting Home Assistant."""
import asyncio
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
                            "colour_2": "blue", "garment_2": "arbitrary text",
                            "colour_3": "yellow", "garment_3": "hat"})
        self.assertEqual(chosen, [("red", "coat"), ("blue", "clothing")])
        self.assertEqual(v.announcement(chosen),
                         "A little Christmas magic! I've lit the bushes to match the red coat and the blue clothing.")

    def test_standout_colours(self):
        self.assertEqual(v.palette({"visible": True, "colour_1": "black",
            "garment_1": "coat", "colour_2": "pink", "garment_2": "dog coat"}),
            [("pink", "dog coat")])
        self.assertEqual(v.palette({"visible": True, "colour_1": "brown",
            "garment_1": "dog", "colour_2": "grey", "garment_2": "coat"}),
            [("brown", "dog")])
        self.assertEqual(v.palette({"visible": True, "colour_1": "red",
            "garment_1": "hat", "colour_2": "red", "garment_2": "scarf"}),
            [("red", "hat")])
        chosen = v.palette({"visible": True, "colour_1": "yellow",
            "garment_1": "scarf", "colour_2": "blue", "garment_2": "dog harness"})
        self.assertEqual(chosen, [("yellow", "scarf"), ("blue", "dog harness")])
        self.assertIn("the yellow scarf and the blue dog harness", v.announcement(chosen))
        self.assertNotIn("colour_3", v.structure())

    def test_night_count(self):
        data = {"visible": False, "counts_reliable": True,
                "people_count": "3", "dog_count": "1"}
        colours, phrase = v.visitor_scene(data)
        self.assertEqual(colours, ["red", "green", "yellow", "light blue", None, None])
        self.assertIn("four bushes", phrase)
        self.assertIn("each of you three", phrase)
        self.assertIn("your dog", phrase)

    def test_night_no_guess(self):
        for data in (
            {"visible": False, "counts_reliable": False, "people_count": "3", "dog_count": "1"},
            {"visible": False, "counts_reliable": True, "people_count": "unknown", "dog_count": "1"},
            {"visible": False, "counts_reliable": True, "people_count": "0", "dog_count": "0"},
            {"visible": False, "counts_reliable": True, "people_count": True, "dog_count": 0},
            {"visible": False, "counts_reliable": True, "people_count": 31, "dog_count": 0},
        ):
            self.assertIsNone(v.visitor_scene(data))

    def test_night_capacity_and_singular(self):
        for people, dogs in ((1, 0), (0, 1), (0, 2), (6, 0), (3, 3), (7, 1)):
            colours, phrase = v.visitor_scene({"visible": False, "counts_reliable": True,
                                               "people_count": people, "dog_count": dogs})
            self.assertEqual(len(colours), 6)
            self.assertEqual(sum(c is not None for c in colours), min(6, people + dogs))
            if people + dogs > 6:
                self.assertIn("all six", phrase)
            elif people + dogs == 1:
                self.assertIn("one bush", phrase)
                self.assertNotIn("one bushes", phrase)

    def test_colour_priority(self):
        colours, phrase = v.visitor_scene({"visible": True, "colour_1": "red",
            "garment_1": "coat", "counts_reliable": True, "people_count": "3", "dog_count": "1"})
        self.assertEqual(colours, ["red"] * 6)
        self.assertIn("red coat", phrase)

    def test_window(self):
        self.assertTrue(v.in_window("16:00", "16:00", "21:00"))
        self.assertFalse(v.in_window("21:00", "16:00", "21:00"))
        self.assertTrue(v.in_window("00:30", "22:00", "02:00"))
        self.assertFalse(v.in_window("12:00", "22:00", "02:00"))
        self.assertTrue(v.in_window("12:00", "00:00", "00:00"))

    def test_master_windows(self):
        hours = ("06:30", "08:30", "16:30", "18:30")
        for t in ("06:30", "08:29", "16:30", "18:29"):
            self.assertTrue(v.master_window(t, *hours))
        for t in ("06:29", "08:30", "12:00", "18:30", "23:00"):
            self.assertFalse(v.master_window(t, *hours))
        self.assertFalse(v.master_window("07:00", "06:30", "06:30", "16:30", "18:30"))
        self.assertTrue(v.master_window("01:00", "06:30", "08:30", "23:00", "02:00"))

    def test_cooldown_and_movies(self):
        self.assertTrue(v.cooldown_ready(100, None, 10))
        self.assertFalse(v.cooldown_ready(699, 100, 10))
        self.assertTrue(v.cooldown_ready(700, 100, 10))
        self.assertTrue(v.cooldown_ready(100, 100, 0))
        self.assertEqual(v.next_movie(["Movie: B", "Movie: A"], "Movie: A"), "Movie: B")
        self.assertEqual(v.next_movie(["Movie: B", "Movie: A"], "Movie: B"), "Movie: A")
        self.assertIsNone(v.next_movie([], None))

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
        visitor.master_open = lambda: visitor.master_enabled
        self.assertFalse(visitor.allowed(0))
        visitor.set_enabled(True)
        self.assertTrue(visitor.allowed(1))
        visitor.master_enabled = False
        self.assertFalse(visitor.allowed(1))
        visitor.master_enabled = True
        show.status = "ready"
        self.assertFalse(visitor.allowed(1))
        show.status = "idle"
        maintenance.enabled = True
        self.assertFalse(visitor.allowed(1))
        maintenance.enabled = False
        visitor.set_enabled(False)
        self.assertFalse(visitor.allowed(1))

class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def make_visitor(self):
        show = SimpleNamespace(status="idle", task=None)
        maintenance = SimpleNamespace(enabled=False)
        visitor = v.VisitorColours(None, {"show": show, "maintenance": maintenance})
        visitor.master_open = lambda: visitor.master_enabled
        visitor.eligible = lambda: True
        visitor._run = AsyncMock(return_value=True)
        visitor.flourish = AsyncMock()
        return visitor

    async def test_night_scene_issues_exact_bush_commands(self):
        show = SimpleNamespace(status="idle", task=None,
                               _lifecycle_lock=asyncio.Lock(), guard=lambda: None,
                               resolve=lambda plan: {})
        calls = AsyncMock(return_value={"data": {"visible": False,
            "counts_reliable": True, "people_count": "3", "dog_count": "1"}})
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda target: SimpleNamespace(state="idle")),
            services=SimpleNamespace(async_call=calls, has_service=lambda *args: True))
        visitor = v.VisitorColours(hass, {"show": show,
            "maintenance": SimpleNamespace(enabled=False)})
        visitor.master_open = lambda: True
        visitor.within_hours = lambda: True
        visitor.set_enabled(True)
        visitor.speak = AsyncMock()
        self.assertTrue(await visitor._run(visitor.generation))
        commands = [call.args for call in calls.await_args_list]
        self.assertEqual(commands[0][:2], ("ai_task", "generate_data"))
        self.assertEqual(len(commands), 7)
        for i, command in enumerate(commands[1:]):
            self.assertEqual(command[:2], ("light", "turn_on" if i < 4 else "turn_off"))
            self.assertEqual(command[2]["entity_id"], v.BUSHES[i])
        self.assertEqual(commands[4][2]["rgb_color"], list(v.COLOURS["light blue"]))
        self.assertIn("four bushes", visitor.speak.await_args.args[0])

    async def test_match_or_fallback(self):
        visitor = self.make_visitor()
        await visitor._route(True)
        visitor._run.assert_awaited_once()
        visitor.flourish.assert_not_awaited()
        self.assertIsNotNone(visitor.last_attempt)
        visitor.eligible = lambda: False
        await visitor._route(True)
        visitor.flourish.assert_awaited_once()

    async def test_master_closed_blocks_both_paths(self):
        visitor = self.make_visitor()
        visitor.master_enabled = False
        await visitor._route(True)
        visitor._run.assert_not_awaited()
        visitor.flourish.assert_not_awaited()
        self.assertIsNone(visitor.last_attempt)

    async def test_failure_and_switch(self):
        visitor = self.make_visitor()
        visitor._run.return_value = False
        await visitor._route(True)
        visitor.flourish.assert_awaited_once()
        visitor.flourish.reset_mock()
        visitor.fallback_enabled = False
        await visitor._route(True)
        visitor.flourish.assert_not_awaited()

    async def test_manual_match_no_fallback(self):
        visitor = self.make_visitor()
        visitor._run.return_value = False
        await visitor._route(False)
        visitor.flourish.assert_not_awaited()

    async def test_flourish_advances_actual_movie(self):
        visitor = self.make_visitor()
        group = SimpleNamespace(lock=asyncio.Lock(), guard=lambda: None,
            validate=AsyncMock(), refresh_movies=AsyncMock(),
            movies={"Movie: A": {}, "Movie: B": {}}, native_effect="Movie: A",
            active=True, effects={"one": "Window Chase"}, play_movie=AsyncMock())
        entity = SimpleNamespace(group=group, entity_id="light.front",
                                 effect_list=["Window Chase"])
        show = visitor.ctx["show"]
        show._lifecycle_lock = asyncio.Lock()
        show.guard = lambda: None
        def resolve(plan):
            plan["targets"] = ["light.front"]
            return {"light.front": entity}
        show.resolve = resolve
        visitor.hass = SimpleNamespace(services=SimpleNamespace(async_call=AsyncMock()))
        with patch.object(v.asyncio, "sleep", AsyncMock()):
            await v.VisitorColours.flourish(visitor)
        group.play_movie.assert_awaited_once_with("Movie: B", round(255 * .65))
        self.assertEqual(visitor.last_movie, "Movie: B")

    async def test_persist_attempt(self):
        visitor = self.make_visitor()
        visitor.store = SimpleNamespace(async_save=AsyncMock(),
            async_load=AsyncMock(return_value={"last_attempt": 100}))
        await visitor.load()
        self.assertEqual(visitor.last_attempt, 100)
        await visitor._route(True)
        visitor.store.async_save.assert_awaited_once_with({"last_attempt": visitor.last_attempt})


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
