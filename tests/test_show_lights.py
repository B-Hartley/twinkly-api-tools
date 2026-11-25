"""Behavioural tests of prepared scenes without Home Assistant or hardware."""
import ast
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state"
light_source = ROOT / "light.py"
light_ast = ast.parse(light_source.read_text())
light_ns = {"asyncio": asyncio, "time": time, "HomeAssistantError": RuntimeError}
exec(compile(ast.Module(body=[node for node in light_ast.body if getattr(node, "name", "") == "Group"], type_ignores=[]), str(light_source), "exec"), light_ns)
Group = light_ns["Group"]
show_source = ROOT / "show_lights.py"
show_ast = ast.parse(show_source.read_text())
show_ns = {
    "asyncio": asyncio, "time": time, "HomeAssistantError": RuntimeError,
    "AsyncExitStack": AsyncExitStack, "asynccontextmanager": asynccontextmanager,
    "_LOGGER": Mock(), "_MODE": "/xled/v1/led/mode",
    "_CHASES": ("Window Chase", "House and Garden Chase"),
}
exec(compile(ast.Module(body=[node for node in show_ast.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))], type_ignores=[]), str(show_source), "exec"), show_ns)
ShowLights = show_ns["ShowLights"]


def make_group(names=("window", "bush"), lock=None):
    group = Group.__new__(Group)
    group.members = [dict(mac=str(index), name=name, role="master" if index == 0 else "slave", width=3, led_count=4) for index, name in enumerate(names)]
    group.master = group.members[0]
    group.lock = lock or asyncio.Lock()
    group.active = False
    group.error = False
    group.hold_idle = False
    group.task = None
    group.native_effect = None
    group.native_brightness = 255
    group.state = {member["mac"]: (False, 255, (255, 255, 255, 0)) for member in group.members}
    group.effects = {key: "Solid" for key in group.state}
    group.patterns = {key: {} for key in group.state}
    group.pane_maps = {"0": [[0, 1], [2, 3]]}
    group.pane_states = {}
    group.pane_effects = {}
    group.pane_patterns = {("0", 0): {"Static rainbow": b"abcdef"}, ("0", 1): {"Static rainbow": b"abcdef"}}
    group.pane_overrides = set()
    group.combined_selected = set()
    group.combined_geometry = {}
    group.chase_started = 0
    group.chase_zones = []
    group.house_zones = []
    group.tokens = {key: "token" for key in group.state}
    group.expiry = {key: time.monotonic() + 600 for key in group.state}
    group.movies = {
        "Movie: Snow": {key: index + 40 for index, key in enumerate(group.state)},
        "Movie: Stars": {key: index + 80 for index, key in enumerate(group.state)},
    }
    group.validate = AsyncMock()
    group.call = AsyncMock(return_value={"mode": "off"})
    group.refresh_movies = AsyncMock()
    group.movie_current_path = AsyncMock(return_value="/xled/v1/movies/current")
    group.login = AsyncMock()
    group.mode = AsyncMock()
    group.native_level = AsyncMock()
    group.send = AsyncMock()
    group.run = AsyncMock()
    group.shutdown = AsyncMock()
    group.notify = Mock()
    return group


def entities(group):
    return {
        "light.front": SimpleNamespace(group=group, effect_list=["Solid", *group.movies]),
        "light.pane_left": SimpleNamespace(group=group, member=group.members[0], index=0, effect_list=["Solid", "Static rainbow"]),
        "light.pane_right": SimpleNamespace(group=group, member=group.members[0], index=1, effect_list=["Solid", "Static rainbow"]),
        "light.bush": SimpleNamespace(group=group, member=group.members[-1], effect_list=["Solid"]),
    }


def plan(targets, movies=True):
    return {
        "targets": list(targets), "soundtrack": {"duration": 140},
        "requires_stored_movies": movies,
        "sections": [{"duration": 140, "cues": ([{"at": 102, "targets": ["light.front"], "stored_movie": True, "movie_index": 0}] if movies else [])}],
    }


class ShowSceneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = SimpleNamespace(async_create_background_task=lambda coroutine, name: asyncio.create_task(coroutine))
        self.group = make_group()
        self.entities = entities(self.group)
        self.player = ShowLights(self.hass, {}, self.entities)

    async def asyncTearDown(self):
        self.group.shutdown.side_effect = None
        await self.player.stop()

    async def prepare(self):
        await self.player.prepare(plan(self.entities))
        self.group.send.reset_mock()
        self.group.mode.reset_mock()
        self.group.call.reset_mock()

    async def test_preflight_and_black_warmup_happen_before_any_scene(self):
        await self.prepare()
        self.group.validate.assert_awaited_once()
        self.group.refresh_movies.assert_awaited_once()
        self.assertEqual(self.group.movie_current_path.await_count, 2)
        self.group.native_level.assert_awaited_once_with(100)
        self.assertTrue(self.group.active)
        self.assertTrue(self.group.hold_idle)
        self.assertFalse(self.group.any_on())

    async def test_previous_movie_is_off_before_brightness_restoration(self):
        self.group.native_effect = "Movie: Snow"
        self.group.native_brightness = 5
        self.group.call.return_value = {"mode": "movie"}
        actions = []
        async def mode(value):
            actions.append(("mode", value))
        async def level(value):
            actions.append(("brightness", value))
        self.group.mode.side_effect = mode
        self.group.native_level.side_effect = level
        await self.player.prepare(plan(self.entities))
        self.assertEqual(actions, [("mode", "off"), ("brightness", 100), ("mode", "rt")])
        self.assertFalse(self.group.any_on())

    async def test_external_realtime_is_never_taken_over(self):
        self.group.call.return_value = {"mode": "rt"}
        with self.assertRaisesRegex(RuntimeError, "external streaming"):
            await self.player.prepare(plan(self.entities))
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()
        self.group.shutdown.assert_not_awaited()

    async def test_missing_required_movies_fails_before_playback_writes(self):
        self.group.movies = {}
        with self.assertRaisesRegex(RuntimeError, "at least one stored movie"):
            await self.player.prepare(plan(self.entities))
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()
        self.group.shutdown.assert_not_awaited()

    async def test_missing_pane_map_fails_before_playback_writes(self):
        self.group.pane_maps.clear()
        with self.assertRaisesRegex(RuntimeError, "pane maps"):
            await self.player.prepare(plan(self.entities))
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()
        self.group.shutdown.assert_not_awaited()

    async def test_late_required_rainbow_is_checked_before_soundtrack(self):
        programme = plan(self.entities)
        programme["sections"][0]["cues"].insert(0, {
            "at": 58, "targets": ["light.pane_left", "light.pane_right"],
            "effect": "Static rainbow", "brightness": 160,
        })
        del self.group.pane_patterns[("0", 1)]["Static rainbow"]
        with self.assertRaisesRegex(RuntimeError, "captured pane map"):
            await self.player.prepare(programme)
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()
        self.group.shutdown.assert_not_awaited()
        self.assertFalse(self.group.pane_states)
        self.assertFalse(self.group.pane_overrides)

    async def test_mixed_movie_and_realtime_scene_rejected_before_writes(self):
        programme = plan(self.entities)
        programme["sections"][0]["cues"].append({
            "at": 102, "targets": ["light.pane_left"], "rgb": [255, 0, 0],
        })
        with self.assertRaisesRegex(RuntimeError, "cannot mix"):
            await self.player.prepare(programme)
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()

    async def test_realtime_after_movie_rejected_before_writes(self):
        programme = plan(self.entities)
        programme["sections"][0]["cues"].append({
            "at": 120, "targets": ["light.pane_left"], "rgb": [255, 0, 0],
        })
        with self.assertRaisesRegex(RuntimeError, "must finish the show"):
            await self.player.prepare(programme)
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()

    async def test_bad_color_level_target_and_movie_index_rejected_before_writes(self):
        for extra, message in [
            ({"rgb": [256, 0, 0]}, "RGB"),
            ({"brightness": -1}, "brightness"),
            ({"targets": ["light.undeclared"]}, "declared"),
            ({"movie_index": -1}, "indices"),
        ]:
            with self.subTest(extra=extra):
                programme = plan(self.entities)
                programme["sections"][0]["cues"].append({
                    "at": 12, "targets": ["light.pane_left"], "rgb": [255, 0, 0], **extra,
                })
                with self.assertRaisesRegex(RuntimeError, message):
                    await self.player.prepare(programme)
        self.group.native_level.assert_not_awaited()
        self.group.mode.assert_not_awaited()
        self.group.validate.assert_not_awaited()

    async def test_failed_warmup_cleans_up_owned_controllers(self):
        self.group.mode.side_effect = RuntimeError("controller failed during warmup")
        with self.assertRaisesRegex(RuntimeError, "warmup"):
            await self.player.prepare(plan(self.entities))
        self.group.shutdown.assert_awaited_once_with("off")
        self.assertFalse(self.group.active)
        self.assertFalse(self.group.hold_idle)
        self.assertFalse(self.player._owned)

    async def test_cancellation_during_warmup_turns_every_owned_group_off(self):
        entered = asyncio.Event()
        async def mode(value):
            entered.set()
            await asyncio.Event().wait()
        self.group.mode.side_effect = mode
        task = asyncio.create_task(self.player.prepare(plan(self.entities)))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.group.shutdown.assert_awaited_once_with("off")
        self.assertFalse(self.group.active)

    async def test_same_time_off_and_two_panes_sends_one_complete_frame(self):
        await self.prepare()
        await self.player.apply([
            {"off": True, "targets": ["light.front"]},
            {"targets": ["light.pane_left"], "brightness": 200, "rgb": [255, 0, 0]},
            {"targets": ["light.pane_right"], "brightness": 100, "rgb": [0, 255, 0]},
        ], 20)
        self.group.send.assert_awaited_once()
        self.group.mode.assert_not_awaited()
        self.group.shutdown.assert_not_awaited()
        self.assertEqual(self.group.pane_states[("0", 0)], (True, 40, (255, 0, 0, 0)))
        self.assertEqual(self.group.pane_states[("0", 1)], (True, 20, (0, 255, 0, 0)))
        self.assertFalse(self.group.member_on("1"))

    async def test_chase_bush_to_window_is_atomic_and_removes_previous_zone(self):
        await self.prepare()
        await self.player.apply([{"targets": ["light.bush"], "rgb": [255, 0, 0]}], 20)
        self.group.send.reset_mock()
        await self.player.apply([
            {"off": True, "targets": ["light.front"]},
            {"targets": ["light.pane_left"], "rgb": [0, 0, 255]},
        ], 20)
        self.assertFalse(self.group.member_on("1"))
        self.assertTrue(self.group.pane_states[("0", 0)][0])
        self.assertFalse(self.group.pane_states[("0", 1)][0])
        self.group.send.assert_awaited_once()
        self.group.mode.assert_not_awaited()

    async def test_combined_bush_halves_share_one_scene(self):
        self.entities["light.combined"] = SimpleNamespace(targets=[(self.group, self.group.members)], effect_list=["Solid"])
        await self.prepare()
        await self.player.apply([{"targets": ["light.combined"], "rgb": [255, 100, 0]}], 10)
        self.assertTrue(all(state[0] for state in self.group.state.values()))
        self.assertEqual({state[2] for state in self.group.state.values()}, {(255, 100, 0, 0)})
        self.group.send.assert_awaited_once()

    async def test_stored_movie_uses_prepared_local_ids_without_slow_reads(self):
        await self.prepare()
        await self.player.apply([{"targets": ["light.front"], "stored_movie": True, "movie_index": 0, "brightness": 255}], 20)
        self.assertFalse(self.group.active)
        self.assertEqual(self.group.native_effect, "Movie: Snow")
        self.group.shutdown.assert_not_awaited()
        self.group.mode.assert_awaited_once_with("movie")
        self.group.validate.assert_awaited_once()
        self.group.refresh_movies.assert_awaited_once()
        self.assertEqual(self.group.call.await_count, 2)
        self.assertEqual([call.kwargs["payload"]["id"] for call in self.group.call.await_args_list], [40, 41])
        self.assertTrue(all(call.kwargs["method"] == "POST" for call in self.group.call.await_args_list))

    async def test_single_movie_does_not_restart_in_second_finale_slot(self):
        self.group.movies.pop("Movie: Stars")
        await self.prepare()
        cue = {"targets": ["light.front"], "stored_movie": True, "movie_index": 0}
        await self.player.apply([cue], 20)
        self.group.mode.reset_mock()
        self.group.call.reset_mock()
        await self.player.apply([{**cue, "movie_index": 1}], 20)
        self.group.mode.assert_not_awaited()
        self.group.call.assert_not_awaited()

    async def test_finished_finale_can_still_be_stopped(self):
        await self.prepare()
        await self.player.apply([{"targets": ["light.front"], "stored_movie": True}], 20)
        await self.player.finish(keep_movie=True)
        await self.player.finish(keep_movie=True)
        self.group.shutdown.assert_not_awaited()
        await self.player.stop()
        await self.player.stop()
        self.group.shutdown.assert_awaited_once_with("off")
        self.assertIsNone(self.group.native_effect)
        self.assertFalse(self.group.active)

    async def test_later_manual_scene_is_not_stopped_with_completed_show(self):
        await self.prepare()
        await self.player.apply([{"targets": ["light.front"], "stored_movie": True}], 20)
        await self.player.finish()
        self.group.native_effect = None
        self.group.active = True
        await self.player.stop()
        self.group.shutdown.assert_not_awaited()

    async def test_off_failure_retains_cleanup_ownership_for_retry(self):
        await self.prepare()
        self.group.shutdown.side_effect = RuntimeError("controller offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await self.player.stop()
        self.assertFalse(self.group.active)
        self.assertFalse(self.group.hold_idle)
        self.assertTrue(self.group.error)
        self.assertIn(self.group, self.player._owned)
        self.group.shutdown.side_effect = None
        await self.player.stop()
        self.assertFalse(self.player._owned)
        self.assertFalse(self.group.error)

    async def test_zero_brightness_does_not_start_movie(self):
        await self.prepare()
        await self.player.apply([{"targets": ["light.front"], "stored_movie": True}], 0)
        self.assertFalse(self.group.any_on())
        self.assertIsNone(self.group.native_effect)
        self.group.mode.assert_not_awaited()

