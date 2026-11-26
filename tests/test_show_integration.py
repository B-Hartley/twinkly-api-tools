"""Run the shipped programme through real show, scene and pixel-rendering code.

Only controller I/O, Sonos state and the clock are simulated. No networking or
Home Assistant installation is needed, and the 140-second show runs instantly.
"""
import ast
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import IntFlag
import importlib.util
import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qsl, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state"


def load_definitions(filename, namespace, names=None):
    path = ROOT / filename
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    if names is not None:
        nodes = [node for node in nodes if getattr(node, "name", None) in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


spec = importlib.util.spec_from_file_location("integration_panes", ROOT / "panes.py")
PANES = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PANES)


class Features(IntFlag):
    EFFECT = 1


class Socket:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def setblocking(self, value):
        pass


class Simulation:
    def __init__(self, *, movies=2, brightness=20, natural_end=False):
        self.now = 0.0
        self.base = datetime.now(timezone.utc)
        self.events = []
        self.frames = []
        self.snapshots = []
        self.tasks = []
        self.brightness = brightness
        self.natural_end = natural_end
        self.plan = json.loads((ROOT / "christmas_show.json").read_text())
        self.speaker = self.plan["speaker"]
        self.states = {self.speaker: SimpleNamespace(state="idle", attributes={"volume_level": 0.3})}
        self.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get),
                                    services=SimpleNamespace(async_call=self.service),
                                    async_create_background_task=self.background)
        timer = SimpleNamespace(monotonic=lambda: self.now)
        transport_asyncio = SimpleNamespace(gather=asyncio.gather, get_running_loop=lambda: self,
                                           sleep=self.sleep)
        light_ns = {"asyncio": transport_asyncio, "time": timer, "HomeAssistantError": RuntimeError,
                    "LightEntity": type("LightEntity", (), {}), "LightEntityFeature": Features,
                    "ColorMode": SimpleNamespace(RGB="rgb", RGBW="rgbw"),
                    "SPIRAL_EFFECTS": (), "SLIDE_CHASE": "Bush and Window Chase V2", "CHASE_EFFECTS": ("Window Chase", "House and Garden Chase", "Bush and Window Chase V2"),
                    "socket": SimpleNamespace(socket=lambda *a: Socket(), AF_INET=2, SOCK_DGRAM=2),
                    "packets": lambda token, frame: [frame], "_LOGGER": Mock()}
        self.light = load_definitions("light.py", light_ns,
            {"Group", "VirtualLight", "PaneLight", "PhysicalGroupLight", "CombinedBush"})
        scene_ns = {"asyncio": asyncio, "time": timer, "math": math, "logging": logging,
                    "AsyncExitStack": AsyncExitStack, "asynccontextmanager": asynccontextmanager,
                    "HomeAssistantError": RuntimeError, "__name__": "show_integration_scenes"}
        self.scene = load_definitions("show_lights.py", scene_ns)
        show_ns = {"__file__": str(ROOT / "show.py"), "asyncio": asyncio, "sleep": self.sleep,
                   "datetime": datetime, "timezone": timezone, "json": json, "logging": logging,
                   "math": math, "Path": Path, "monotonic": lambda: self.now,
                   "parse_qsl": parse_qsl, "unquote": unquote, "urlsplit": urlsplit,
                   "HomeAssistantError": RuntimeError, "ShowLights": self.scene.ShowLights,
                   "__name__": "show_integration_controller"}
        self.controller = load_definitions("show.py", show_ns)
        original_position = show_ns["media_position"]
        show_ns["media_position"] = lambda state, now=None: original_position(state, now or self.utc())
        self.group = self.make_group(movies)
        self.entities = self.make_entities()
        self.states.update({key: SimpleNamespace(state="off", attributes={}) for key in self.entities})
        self.group.entities = list(self.entities.values())
        self.show = self.controller.Show(self.hass, {"virtual_groups": [self.group]})
        resolved = self.show.resolve(self.plan)
        self.show.lights = self.scene.ShowLights(self.hass, self.show.ctx, resolved)

    def utc(self):
        return self.base + timedelta(seconds=self.now)

    async def sleep(self, seconds):
        self.now += seconds
        if self.natural_end and self.now >= 141.8 and self.states[self.speaker].state == "playing":
            self.states[self.speaker].state = "paused"
            self.states[self.speaker].attributes.update(media_position=0, media_position_updated_at=self.utc())
        await asyncio.sleep(0)

    def background(self, coroutine, name):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def sock_sendto(self, sock, payload, address):
        self.frames.append((self.now, address[0], payload))

    async def service(self, domain, action, data, blocking=True):
        self.events.append((self.now, action, data))
        if action == "play_media":
            self.states[self.speaker] = SimpleNamespace(state="playing", attributes={
                "media_content_id": data["media_content_id"], "media_position": 0,
                "media_position_updated_at": self.utc(), "media_duration": self.plan["soundtrack"]["duration"],
                "volume_level": 0.05,
            })
        elif action == "media_stop":
            self.states[self.speaker].state = "idle"

    def make_group(self, movie_count):
        group = self.light.Group.__new__(self.light.Group)
        definitions = [
            ("office left", 210, 3), ("office right", 210, 3),
            ("master bedroom", 210, 3), ("landing", 210, 3),
            ("lounge", 210, 3), ("bar", 210, 3),
            ("front left top", 60, 4), ("front left bottom", 60, 4),
            ("back left bush", 60, 3), ("back bush top", 60, 4),
            ("back bush bottom", 60, 4), ("mid right", 60, 3),
            ("front right bush", 600, 4), ("light tree", 60, 3), ("icicles", 60, 3),
        ]
        group.members = [dict(mac=f"{i:012x}", name=name, led_count=count, width=width,
                              role="master" if i == 0 else "slave", ip=f"controller-{i}")
                         for i, (name, count, width) in enumerate(definitions)]
        group.master = group.members[0]
        group.lock = asyncio.Lock()
        group.active = False
        group.error = False
        group.hold_idle = False
        group.task = None
        group.native_effect = None
        group.native_brightness = 255
        group.state = {m["mac"]: (False, 255, (255, 255, 255, 0)) for m in group.members}
        group.effects = {mac: "Solid" for mac in group.state}
        group.patterns = {mac: {} for mac in group.state}
        group.pane_maps = {m["mac"]: PANES.pane_indices(m["name"], m["led_count"])
                           for m in group.members if m["name"] in PANES.DEFAULTS}
        group.pane_states = {}
        group.pane_effects = {}
        group.pane_patterns = {}
        for member in group.members:
            for index, pixels in enumerate(group.pane_maps.get(member["mac"], [])):
                colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
                values = [(0, *colors[p % 3]) if member["width"] == 4 else colors[p % 3]
                          for p in range(len(pixels))]
                group.pane_patterns[(member["mac"], index)] = {
                    "Static rainbow": bytes(channel for value in values for channel in value),
                    "Static rainbow reversed": bytes(channel for value in reversed(values) for channel in value),
                }
        group.geometry = {}
        group.pane_geometry = {}
        group.pane_overrides = set()
        group.combined_selected = set()
        group.combined_geometry = {}
        group.chase_started = 0
        group.chase_zones = []
        group.house_zones = []
        group.tokens = {mac: "token" for mac in group.state}
        group.expiry = {mac: 1000 for mac in group.state}
        group.movies = {name: {m["mac"]: offset + i for i, m in enumerate(group.members)}
                        for name, offset in [("Movie: Snow", 40), ("Movie: Stars", 80)][:movie_count]}
        group.validate = AsyncMock()
        group.call = AsyncMock(side_effect=self.controller_call)
        group.refresh_movies = AsyncMock()
        group.movie_current_path = AsyncMock(return_value="/xled/v1/movies/current")
        group.login = AsyncMock()
        group.mode = AsyncMock(side_effect=self.mode)
        group.native_level = AsyncMock(side_effect=lambda level: self.events.append((self.now, "native_level", level)))
        group.run = AsyncMock()  # The scheduler explicitly renders every real scene.
        group.shutdown = AsyncMock(side_effect=lambda mode: self.events.append((self.now, "shutdown", mode)))
        group.notify = Mock(side_effect=self.snapshot)
        return group

    async def controller_call(self, member, path, **kwargs):
        if kwargs.get("method") == "POST":
            self.events.append((self.now, "controller_post", (member["mac"], path, kwargs["payload"])))
        return {"mode": "off"}

    async def mode(self, value):
        self.events.append((self.now, "mode", value))
        if value == "rt":
            self.now += 2  # Simulate the existing Twinkly black-frame warmup.

    def make_entities(self):
        entities = {}
        by_name = {m["name"]: m for m in self.group.members}
        window_names = ("office left", "office right", "master bedroom", "landing", "lounge", "bar")
        for name in (*window_names, "front right bush"):
            member = by_name[name]
            for index in range(len(self.group.pane_maps[member["mac"]])):
                label = PANES.pane_label(name, index).lower().replace(" ", "_")
                stem = "front_right" if name == "front right bush" else name.replace(" ", "_")
                key = f"light.twinkly_{stem}_{label}"
                entity = self.light.PaneLight.__new__(self.light.PaneLight)
                entity.group, entity.member, entity.index = self.group, member, index
                entity._attr_supported_features = Features.EFFECT
                entity.show_key = entity.entity_id = key
                entities[key] = entity
        for stem, names in (("front_left_bush", ("front left top", "front left bottom")),
                            ("back_right_bush", ("back bush top", "back bush bottom"))):
            entity = self.light.CombinedBush.__new__(self.light.CombinedBush)
            entity.targets = [(self.group, [by_name[name] for name in names])]
            entity.parts = [(self.group, by_name[name]) for name in names]
            entity.combined_map = None
            entity.show_key = entity.entity_id = "light.twinkly_" + stem
            entities[entity.entity_id] = entity
        for name in ("back left bush", "mid right", "light tree", "icicles"):
            entity = self.light.VirtualLight.__new__(self.light.VirtualLight)
            entity.group, entity.member = self.group, by_name[name]
            entity.show_key = entity.entity_id = "light.twinkly_" + name.replace(" ", "_")
            entities[entity.entity_id] = entity
        entity = self.light.PhysicalGroupLight.__new__(self.light.PhysicalGroupLight)
        entity.group = self.group
        entity.show_key = entity.entity_id = "light.twinkly_front"
        entities[entity.entity_id] = entity
        return entities

    def snapshot(self):
        self.snapshots.append((self.now, {key: entity.is_on for key, entity in self.entities.items()},
                               self.group.native_effect, self.group.active))

    async def run(self):
        await self.show.run(self.plan, {"audio_base": "https://example.test/local/christmas", "brightness_pct": self.brightness})
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def frame_at(self, at, member_name):
        ip = next(m["ip"] for m in self.group.members if m["name"] == member_name)
        return next(payload for when, address, payload in reversed(self.frames)
                    if abs(when - (at + 2)) < 0.00001 and address == ip)


class FullShowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_countdown_sync_with_no_movies_and_only_landing_lit(self):
        sim = Simulation(movies=0,brightness=100)
        sim.plan=json.loads((ROOT/"countdown_test.json").read_text())
        member=next(m for m in sim.group.members if m["name"]=="landing")
        spec=importlib.util.spec_from_file_location("integration_countdown",ROOT/"countdown.py")
        digits=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(digits)
        layout={"source":"2d","coordinates":[{"x":i/9,"y":j/20} for i in range(10) for j in range(21)]}
        patterns=digits.countdown_patterns(layout,210,3)
        sim.group.patterns[member["mac"]].update(patterns)
        entity=sim.light.VirtualLight.__new__(sim.light.VirtualLight)
        entity.group,entity.member=sim.group,member
        entity.show_key=entity.entity_id="light.twinkly_landing"
        sim.group.entities.append(entity)
        sim.states[entity.entity_id]=SimpleNamespace(state="off",attributes={})
        resolved=sim.show.resolve(sim.plan)
        sim.show.lights=sim.scene.ShowLights(sim.hass,sim.show.ctx,resolved)
        await sim.run()
        self.assertIsNone(sim.show.last_error)
        self.assertAlmostEqual(sim.now,12)
        self.assertEqual([at for at,action,data in sim.events if action=="play_media"],[2])
        for at,digit in enumerate((5,4,3,2,1),3):
            self.assertEqual(sim.frame_at(at,"landing"),patterns[f"Countdown {digit}"])
            for other in sim.group.members:
                if other is not member:
                    self.assertFalse(any(sim.frame_at(at,other["name"])))
        self.assertFalse(any(sim.frame_at(8,"landing")))
        sim.group.shutdown.assert_awaited_once_with("off")
        self.assertFalse(sim.group.active)
        self.assertIsNone(sim.group.native_effect)

    async def test_shipped_show_real_entities_render_and_two_movie_finale(self):
        sim = Simulation()
        self.assertEqual(len(sim.group.members), 15)
        self.assertEqual(len([e for e in sim.entities.values() if isinstance(e, sim.light.PaneLight)]), 19)
        await sim.run()
        self.assertIsNone(sim.show.last_error)
        self.assertAlmostEqual(sim.now, 142)
        self.assertEqual([data for _, action, data in sim.events if action == "mode"], ["off", "rt", "movie", "movie"])
        self.assertEqual([round(at - 2, 4) for at, action, data in sim.events if action == "mode" and data == "movie"],
                         [102.2026, 119.0603])
        self.assertEqual(len([event for event in sim.events if event[1] == "play_media"]), 1)
        self.assertEqual(sim.group.native_effect, "Movie: Stars")
        self.assertFalse(sim.group.active)
        self.assertFalse(sim.group.hold_idle)
        self.assertEqual(sim.group.validate.await_count, 1)
        self.assertEqual(sim.group.refresh_movies.await_count, 1)
        self.assertEqual(sim.group.movie_current_path.await_count, 15)
        selections = [data for _, action, data in sim.events if action == "controller_post"]
        self.assertEqual(len(selections), 30)
        self.assertEqual([selection[2]["id"] for selection in selections], list(range(40, 55)) + list(range(80, 95)))
        # First narrated bushes light both physical halves together, with windows dark.
        self.assertTrue(any(sim.frame_at(9.0093, "front left top")))
        self.assertTrue(any(sim.frame_at(9.0093, "front left bottom")))
        self.assertEqual(max(sim.frame_at(9.0093, "front left top")), 23)
        for name in ("office left", "office right", "master bedroom", "landing", "lounge", "bar"):
            self.assertFalse(any(sim.frame_at(9.0093, name)))
        # The first window scene completely replaces bushes and illuminates only pane 1.
        pixels = sim.frame_at(19.3654, "lounge")
        self.assertTrue(any(pixels[-63 * 3:]))
        self.assertFalse(any(pixels[:-63 * 3]))
        self.assertFalse(any(sim.frame_at(19.3654, "front left top")))
        # A many-target colour arrangement emits exactly one final frame per
        # physical controller, despite its same-time all-off and colour cues.
        self.assertEqual(len([frame for frame in sim.frames if abs(frame[0] - 44.0629) < 0.00001]), 15)
        for member in sim.group.members[:6]:
            frame = sim.frame_at(71.4733, member["name"])
            for pane in sim.group.pane_maps[member["mac"]]:
                colors = {frame[pixel * 3:(pixel + 1) * 3] for pixel in pane}
                self.assertGreaterEqual(len(colors), 3)
                self.assertNotIn(bytes(3), colors)
        self.assertTrue(any(sim.frame_at(90.9572, "lounge")))
        self.assertFalse(any(sim.frame_at(90.9572, "front left top")))
        # The split front-right controller retains two separate 300-pixel bush zones.
        right_key = "light.twinkly_front_right_left_bush"
        left_scene = next(section["start"] + cue["at"] for section in sim.plan["sections"][:3]
                          for cue in section["cues"] if cue.get("targets") == [right_key] and not cue.get("off"))
        front_right = sim.frame_at(left_scene, "front right bush")
        self.assertEqual(bool(any(front_right[:300 * 4])), False)  # Actual default map is reversed.
        self.assertTrue(any(front_right[300 * 4:]))
        await sim.show.stop()
        sim.group.shutdown.assert_awaited_once_with("off")
        self.assertIsNone(sim.group.native_effect)

    async def test_one_stored_movie_continues_without_restart(self):
        sim = Simulation(movies=1)
        await sim.run()
        self.assertIsNone(sim.show.last_error)
        self.assertEqual(sim.group.native_effect, "Movie: Snow")
        self.assertEqual([data for _, action, data in sim.events if action == "mode"], ["off", "rt", "movie"])
        await sim.show.stop()

    async def test_natural_sonos_stopped_as_paused_keeps_second_movie(self):
        sim = Simulation(natural_end=True)
        await sim.run()
        self.assertIsNone(sim.show.last_error)
        self.assertTrue(sim.show._audio_ended)
        self.assertEqual(sim.group.native_effect, "Movie: Stars")
        sim.group.shutdown.assert_not_awaited()
        await sim.show.stop()

    async def test_real_plan_bad_late_rainbow_fails_before_audio_or_playback_writes(self):
        sim = Simulation()
        member = next(m for m in sim.group.members if m["name"] == "bar")
        del sim.group.pane_patterns[(member["mac"], 3)]["Static rainbow reversed"]
        with self.assertLogs("show_integration_controller", level="ERROR"):
            await sim.run()
        self.assertIsNotNone(sim.show.last_error)
        self.assertEqual(sim.events, [])
        self.assertEqual(sim.frames, [])
        self.assertFalse(sim.group.pane_states)
        sim.group.shutdown.assert_not_awaited()

    async def test_real_plan_missing_movies_fails_before_audio_or_playback_writes(self):
        sim = Simulation(movies=0)
        with self.assertLogs("show_integration_controller", level="ERROR"):
            await sim.run()
        self.assertIn("stored movie", sim.show.last_error)
        self.assertEqual(sim.events, [])
        sim.group.shutdown.assert_not_awaited()

    async def test_real_plan_zero_brightness_never_renders_lit_pixel_or_movie(self):
        sim = Simulation(brightness=0)
        await sim.run()
        self.assertIsNone(sim.show.last_error)
        self.assertFalse(any(any(payload) for _, _, payload in sim.frames))
        self.assertEqual([data for _, action, data in sim.events if action == "mode"], ["off", "rt"])
        sim.group.shutdown.assert_awaited_once_with("off")
        self.assertIsNone(sim.group.native_effect)

    async def test_real_plan_cancellation_during_warmup_cleans_up_before_audio(self):
        sim = Simulation()
        async def cancelled_mode(value):
            raise asyncio.CancelledError()
        sim.group.mode.side_effect = cancelled_mode
        with self.assertRaises(asyncio.CancelledError):
            await sim.run()
        self.assertFalse(any(action == "play_media" for _, action, _ in sim.events))
        sim.group.shutdown.assert_awaited_once_with("off")
        self.assertFalse(sim.group.active)
        self.assertFalse(sim.group.hold_idle)
        self.assertFalse(sim.show.lights._owned)


if __name__ == "__main__":
    unittest.main()
