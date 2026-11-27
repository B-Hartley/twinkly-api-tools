"""Exercise the complete optional show through native pixels and house services."""
import asyncio
from copy import deepcopy
import importlib.util
import json
import logging
import math
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_show_integration import ROOT, Simulation, load_definitions


MOVIES = ["Eclipse", "UpDown", "Bright Twinkle", "Vertical Flag", "Night Sky", "Christmas Spirit"]


class FakeContext:
    def __init__(self, ident="show-house", user_id=None):
        self.id = ident
        self.user_id = user_id


class HouseSimulation(Simulation):
    """Reuse the real scene/pixel harness and add capability-aware HA light I/O."""

    def __init__(self, *, brightness=2, missing_movie=None):
        super().__init__(brightness=brightness)
        self.plan = json.loads((ROOT / "christmas_show_house_chase.json").read_text())
        self.house_events = []
        self.house_initial = {}
        self.house_leaves = {}
        self.fail_house_at = None
        self.watch_tick = asyncio.Event()
        self.stream_hold = asyncio.Event()
        self.group.expiry = {mac: 10000 for mac in self.group.state}
        self.group.movies = {
            "Movie: " + name: {member["mac"]: 100 * (index + 1) + offset
                              for offset, member in enumerate(self.group.members)}
            for index, name in enumerate(MOVIES) if name != missing_movie
        }
        self.group.run = AsyncMock(side_effect=self.stream_hold.wait)
        self.add_landing_entity()
        self.add_house_entities()
        self.hass.async_add_executor_job = AsyncMock(side_effect=lambda job: job())
        house_namespace = {
            "__name__": "house_show_integration_rooms", "asyncio": asyncio,
            "deepcopy": deepcopy, "logging": logging, "math": math,
            "HomeAssistantError": RuntimeError, "Context": FakeContext,
        }
        house_module = load_definitions("house_lights.py", house_namespace)
        self.show.__class__.__init__.__globals__["HouseLights"] = house_module.HouseLights

    def add_landing_entity(self):
        member = next(member for member in self.group.members if member["name"] == "landing")
        spec = importlib.util.spec_from_file_location("house_integration_countdown", ROOT / "countdown.py")
        digits = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(digits)
        layout = {"source": "2d", "coordinates": [
            {"x": i / 9, "y": j / 20} for i in range(10) for j in range(21)
        ]}
        self.digit_patterns = digits.countdown_patterns(layout, 210, 3)
        self.group.patterns[member["mac"]].update(self.digit_patterns)
        entity = self.light.VirtualLight.__new__(self.light.VirtualLight)
        entity.group, entity.member = self.group, member
        entity.show_key = entity.entity_id = "light.twinkly_landing"
        self.entities[entity.entity_id] = entity
        self.group.entities.append(entity)
        self.states[entity.entity_id] = SimpleNamespace(state="off", attributes={})

    def add_house_entities(self):
        for key, configured in self.plan["house_rooms"].items():
            groups = [configured] if isinstance(configured, str) else configured
            leaves = []
            for index, group_id in enumerate(groups):
                # Every room exercises colour and white leaves; two rooms also
                # exercise lamps which cannot accept brightness or colour.
                colour_id, white_id = group_id + "_colour", group_id + "_white"
                members = [colour_id, white_id]
                self.states[colour_id] = SimpleNamespace(state="on", context=FakeContext("before"), attributes={
                    "supported_color_modes": ["xy", "color_temp"], "supported_features": 32,
                    "color_mode": "xy", "xy_color": (.3, .4), "brightness": 93,
                })
                self.states[white_id] = SimpleNamespace(state="on" if index == 0 else "off",
                    context=FakeContext("before"), attributes={
                        "supported_color_modes": ["color_temp"], "supported_features": 32,
                        "color_mode": "color_temp", "color_temp_kelvin": 2700,
                        "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6000,
                        "brightness": 120,
                    })
                if key in ("house:landing", "house:master_bedroom"):
                    lamp_id = group_id + "_switch"
                    self.states[lamp_id] = SimpleNamespace(state="off", context=FakeContext("before"),
                        attributes={"supported_color_modes": ["onoff"], "color_mode": "onoff"})
                    members.append(lamp_id)
                self.states[group_id] = SimpleNamespace(state="on", attributes={"entity_id": set(members)})
                leaves.extend(members)
            self.house_leaves[key] = leaves
            for leaf in leaves:
                self.house_initial[leaf] = deepcopy(self.states[leaf])

    async def sleep(self, seconds):
        if asyncio.current_task().get_name() == "Christmas ready timeout":
            await self.watch_tick.wait()
            self.watch_tick.clear()
        else:
            await super().sleep(seconds)

    def background(self, coroutine, name):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    async def service(self, domain, action, data, blocking=True, context=None):
        if domain != "light":
            if action == "media_play":
                self.events.append((self.now, action, data))
                self.states[self.speaker].state = "playing"
                self.states[self.speaker].attributes["media_position_updated_at"] = self.utc()
            elif action == "media_pause":
                self.events.append((self.now, action, data))
                self.states[self.speaker].state = "paused"
                self.states[self.speaker].attributes["media_position_updated_at"] = self.utc()
            elif action == "media_seek":
                self.events.append((self.now, action, data))
                self.states[self.speaker].attributes["media_position"] = data["seek_position"]
            else:
                await super().service(domain, action, data, blocking)
                if action == "volume_set":
                    self.states[self.speaker].attributes["volume_level"] = data["volume_level"]
            return
        self.house_events.append((self.now, action, deepcopy(data)))
        if self.fail_house_at is not None and action == "turn_on" and self.now >= self.fail_house_at:
            self.fail_house_at = None
            raise RuntimeError("Simulated house bridge failure")
        for entity_id in data["entity_id"]:
            state = self.states[entity_id]
            modes = set(state.attributes["supported_color_modes"])
            if "brightness" in data and modes == {"onoff"}:
                raise AssertionError("Undimmable lamp received brightness")
            if "rgb_color" in data and not modes.intersection({"xy", "rgb", "hs", "rgbw", "rgbww"}):
                raise AssertionError("White-only bulb received RGB")
            state.state = "on" if action == "turn_on" else "off"
            state.attributes.update({key: value for key, value in data.items()
                                     if key not in ("entity_id", "transition")})
            if "rgb_color" in data:
                state.attributes["color_mode"] = "rgb"
            elif "xy_color" in data:
                state.attributes["color_mode"] = "xy"
            elif "color_temp_kelvin" in data:
                state.attributes["color_mode"] = "color_temp"
            state.context = context or FakeContext("service")

    async def prepare(self):
        await self.show.prepare(SimpleNamespace(data={
            "variant": "house_chase", "audio_base": "https://example.test/local/christmas",
            "brightness_pct": self.brightness, "house_brightness_pct": 100,
        }))
        await self.show.task

    async def go(self):
        await self.show.start_prepared(SimpleNamespace(data={}))
        await self.show.task
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def close(self):
        await self.show.stop()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class HouseShowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def make_sim(self, **options):
        sim = HouseSimulation(**options)
        self.addAsyncCleanup(sim.close)
        return sim

    def assert_restored(self, sim):
        for entity_id, before in sim.house_initial.items():
            after = sim.states[entity_id]
            self.assertEqual(after.state, before.state, entity_id)
            if before.state == "on":
                for key in ("brightness", "xy_color", "color_temp_kelvin", "color_mode"):
                    if key in before.attributes:
                        self.assertEqual(after.attributes[key], before.attributes[key], (entity_id, key))

    def assert_only_target_pixels(self, sim, at, target):
        expected = {}
        for _, members, index, _ in sim.scene.ShowLights._bindings(sim.entities[target]):
            for member in members:
                expected[member["mac"]] = (set(range(member["led_count"])) if index is None else
                                             set(sim.group.pane_maps[member["mac"]][index]))
        for member in sim.group.members:
            frame = sim.frame_at(at, member["name"])
            lit = {pixel for pixel in range(member["led_count"])
                   if any(frame[pixel * member["width"]:(pixel + 1) * member["width"]])}
            self.assertEqual(lit, expected.get(member["mac"], set()), (at, target, member["name"]))
        frames = [frame for frame in sim.frames if abs(frame[0] - (at + 2)) < .00001]
        self.assertEqual(len(frames), len(sim.group.members), ("non-atomic scene", at))

    async def test_full_prepared_house_show_and_six_named_movies(self):
        sim = self.make_sim()
        await sim.prepare()
        self.assertEqual(sim.show.status, "ready", sim.show.last_error)
        self.assertEqual(sim.states[sim.speaker].state, "paused")
        self.assertEqual(sim.house_events, [])
        self.assert_restored(sim)
        await sim.go()
        self.assertIsNone(sim.show.last_error)
        self.assertAlmostEqual(sim.now, 142)
        self.assertEqual(sim.show.status, "finished")
        self.assertEqual(len([e for e in sim.events if e[1] == "play_media"]), 1)
        self.assertEqual(len([e for e in sim.events if e[1] == "media_play"]), 1)
        movie_starts = [at - 2 for at, action, data in sim.events if action == "mode" and data == "movie"]
        self.assertEqual(movie_starts, [99, 104, 109, 114, 119, 124])
        selected = []
        for _, _, movie, _ in sim.snapshots:
            if movie and (not selected or movie != selected[-1]):
                selected.append(movie)
        self.assertEqual(selected, ["Movie: " + name for name in MOVIES])
        self.assertEqual(sim.group.native_effect, "Movie: Christmas Spirit")
        self.assertEqual(sim.group.native_brightness, 5)  # Twinkly 2%, independent of house 100%.
        self.assertEqual(len([event for event in sim.events if event[1] == "controller_post"]), 90)
        sim.group.validate.assert_awaited_once()
        sim.group.refresh_movies.assert_awaited_once()
        self.assert_restored(sim)

        # All nine named groups take their turn at the maximum available output.
        route = sim.plan["routes"]["house_chase"]
        self.assertEqual(len(route), 9)
        for index, key in enumerate(route):
            at = 54 + 2 * index  # Two-second device warmup precedes soundtrack zero.
            commands = [data for when, action, data in sim.house_events
                        if abs(when - at) < .00001 and action == "turn_on"]
            lit = {entity for data in commands for entity in data["entity_id"]}
            self.assertEqual(lit, set(sim.house_leaves[key]), (key, commands))
            for data in commands:
                if "brightness" in data:
                    self.assertEqual(data["brightness"], 255)

    async def test_every_bush_and_pane_startup_is_whole_and_atomic(self):
        sim = self.make_sim()
        await sim.prepare()
        await sim.go()
        for index, target in enumerate(sim.plan["zones"]["bushes"]):
            self.assert_only_target_pixels(sim, 9 + 1.25 * index, target)
        self.assertEqual(len(sim.plan["zones"]["bushes"]), 6)
        for index, target in enumerate(sim.plan["zones"]["windows"]):
            self.assert_only_target_pixels(sim, 19 + .92 * index, target)
        self.assertEqual(len(sim.plan["zones"]["windows"]), 17)

    async def test_landing_digits_and_crash_recovery_keep_one_live_clock(self):
        sim = self.make_sim(brightness=100)
        await sim.prepare()
        await sim.go()
        for at, digit in enumerate((5, 4, 3, 2, 1), 94):
            self.assertEqual(sim.frame_at(at, "landing"), sim.digit_patterns[f"Countdown {digit}"])
            for member in sim.group.members:
                if member["name"] != "landing":
                    self.assertFalse(any(sim.frame_at(at, member["name"])))
        for at, expected in ((72, 95), (74, 50), (76, 18)):
            self.assertEqual(max(max(sim.frame_at(at, member["name"])) for member in sim.group.members), expected)
        for member in sim.group.members:
            self.assertFalse(any(sim.frame_at(77.2, member["name"])))
        self.assert_only_target_pixels(sim, 80, "light.twinkly_light_tree")
        self.assertTrue(all(any(sim.frame_at(84, member["name"])) for member in sim.group.members))
        self.assertEqual([data for _, action, data in sim.events if action == "mode"],
                         ["off", "rt"] + ["movie"] * 6)
        self.assertIsNone(sim.show.last_error)

    async def test_missing_named_movie_fails_before_audio_or_house_changes(self):
        sim = self.make_sim(missing_movie="Vertical Flag")
        with self.assertLogs("show_integration_controller", level="ERROR"):
            await sim.prepare()
        self.assertEqual(sim.show.status, "error")
        self.assertIn("Vertical Flag", sim.show.last_error)
        self.assertFalse(any(action in ("play_media", "media_play", "volume_set", "mode")
                             for _, action, _ in sim.events))
        self.assertEqual(sim.house_events, [])
        self.assert_restored(sim)

    async def test_missing_landing_number_map_fails_during_prepare(self):
        sim = self.make_sim()
        landing = next(member for member in sim.group.members if member["name"] == "landing")
        sim.group.patterns[landing["mac"]].pop("Countdown 3")
        with self.assertLogs("show_integration_controller", level="ERROR"):
            await sim.prepare()
        self.assertEqual(sim.show.status, "error")
        self.assertIn("Countdown 3", sim.show.last_error)
        self.assertFalse(any(action in ("play_media", "media_play", "volume_set")
                             for _, action, _ in sim.events))
        self.assertEqual(sim.house_events, [])

    async def test_house_bridge_failure_stops_owned_audio_and_restores_every_room(self):
        sim = self.make_sim()
        await sim.prepare()
        sim.fail_house_at = 54
        with self.assertLogs(level="ERROR"):
            await sim.go()
        self.assertEqual(sim.show.status, "error")
        self.assertIn("house bridge failure", sim.show.last_error)
        self.assertTrue(any(action == "media_stop" for _, action, _ in sim.events))
        self.assertFalse(sim.group.active)
        self.assertIsNone(sim.group.native_effect)
        self.assert_restored(sim)


if __name__ == "__main__":
    unittest.main()
