"""Offline lifecycle/timing tests without a Home Assistant installation."""
import ast
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qsl, unquote, urlsplit


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state/show.py"
TREE = ast.parse(SOURCE.read_text())
MODULE = SimpleNamespace()
NAMESPACE = MODULE.__dict__
NAMESPACE.update({
    "__file__": str(SOURCE), "asyncio": asyncio, "sleep": asyncio.sleep,
    "datetime": datetime, "timezone": timezone, "json": json,
    "logging": logging, "math": math, "Path": Path, "monotonic": monotonic,
    "parse_qsl": parse_qsl, "unquote": unquote, "urlsplit": urlsplit,
    "HomeAssistantError": RuntimeError, "ShowLights": Mock(),
    "__name__": "show_sync_tests",
})
exec(compile(ast.Module(body=[node for node in TREE.body
                             if not isinstance(node, (ast.Import, ast.ImportFrom))],
                        type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)
Show = MODULE.Show
URL = "https://example.test/local/christmas/christmas_show_v2.mp3"
SPEAKER = "media_player.test_sonos"


def plan():
    return {
        "speaker": SPEAKER, "soundtrack": {"file": "christmas_show_v2.mp3", "duration": 2},
        "targets": ["light.bush", "light.window"],
        "sections": [
            {"id": "bushes", "duration": 1, "cues": [
                {"at": 0, "off": True, "targets": ["light.bush", "light.window"]},
                {"at": 0.5, "targets": ["light.bush"], "rgb": [255, 0, 0], "brightness": 100}]},
            {"id": "windows", "duration": 1, "cues": [
                {"at": 0, "off": True, "targets": ["light.bush"]},
                {"at": 0, "targets": ["light.window"], "rgb": [0, 255, 0], "brightness": 100}]},
        ],
    }


def state(mode="idle", **attributes):
    return SimpleNamespace(state=mode, attributes=attributes)


class Clock:
    def __init__(self):
        self.value = 0.0
        self.base = datetime.now(timezone.utc)
        self.hook = None

    def now(self):
        return self.value

    def utc(self):
        return self.base + timedelta(seconds=self.value)

    async def sleep(self, seconds):
        self.value += seconds
        if self.hook:
            self.hook()


class ShowSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.states = {SPEAKER: state(volume_level=0.3),
                       "light.bush": state("off"), "light.window": state("off")}
        self.hass = SimpleNamespace(
            states=SimpleNamespace(get=self.states.get),
            services=SimpleNamespace(async_call=AsyncMock()),
            async_create_background_task=Mock(),
            async_add_executor_job=AsyncMock(),
        )
        self.show = Show(self.hass, {})
        self.lights = SimpleNamespace(prepare=AsyncMock(), apply=AsyncMock(),
                                      finish=AsyncMock(), stop=AsyncMock())
        self.show.lights = self.lights
        self.time_patch = patch.object(MODULE, "monotonic", self.clock.now)
        self.sleep_patch = patch.object(MODULE, "sleep", self.clock.sleep)
        original_position = MODULE.media_position
        self.position_patch = patch.object(
            MODULE, "media_position", lambda value, now=None: original_position(value, now or self.clock.utc()))
        self.time_patch.start()
        self.sleep_patch.start()
        self.position_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.addCleanup(self.sleep_patch.stop)
        self.addCleanup(self.position_patch.stop)

    def playing(self, position=0, updated=None, url=URL):
        self.states[SPEAKER] = state(
            "playing", media_content_id=url, media_position=position,
            media_position_updated_at=updated or self.clock.utc(), media_duration=2,
            volume_level=0.05)

    def test_timeline_groups_equal_times_across_chapters(self):
        timeline, duration = MODULE.build_timeline(plan())
        self.assertEqual(duration, 2)
        self.assertEqual([at for at, _ in timeline], [0, 0.5, 1])
        self.assertEqual(len(timeline[-1][1]), 2)
        self.assertTrue(timeline[-1][1][0]["off"])
        self.assertEqual(timeline[-1][1][1]["targets"], ["light.window"])

    def test_plan_rejects_wrong_duration_and_undeclared_targets(self):
        broken = plan()
        broken["soundtrack"]["duration"] = 3
        with self.assertRaisesRegex(RuntimeError, "durations differ"):
            MODULE.build_timeline(broken)
        broken = plan()
        broken["sections"][0]["cues"][0]["targets"] = ["light.other"]
        with self.assertRaisesRegex(RuntimeError, "undeclared"):
            MODULE.build_timeline(broken)

    def test_uri_normalization_preserves_version(self):
        self.assertEqual(MODULE.media_identity(URL.replace("example.test", "EXAMPLE.TEST") + "?v=2&a=b"),
                         MODULE.media_identity(URL + "?a=b&amp;v=2"))
        self.assertNotEqual(MODULE.media_identity(URL + "?v=2"), MODULE.media_identity(URL + "?v=1"))

    def test_audio_required_does_not_silently_downgrade(self):
        with self.assertRaisesRegex(RuntimeError, "audio_base"):
            self.show.audio_ready(plan(), {})
        self.assertIsNone(self.show.audio_ready(plan(), {"audio": False}))
        self.playing()
        with self.assertRaisesRegex(RuntimeError, "idle or paused"):
            self.show.audio_ready(plan(), {"audio_base": URL.rsplit("/", 1)[0]})

    def test_paused_ungrouped_speaker_allowed(self):
        self.states[SPEAKER] = state("paused", group_members=[SPEAKER])
        self.assertEqual(self.show.audio_ready(plan(), {"audio_base": URL.rsplit("/", 1)[0]}), URL)
        self.states[SPEAKER].attributes["group_members"].append("media_player.other")
        with self.assertRaisesRegex(RuntimeError, "Ungroup"):
            self.show.audio_ready(plan(), {"audio_base": URL.rsplit("/", 1)[0]})

    async def test_cached_same_track_position_is_not_start_confirmation(self):
        requested = self.clock.utc()
        self.playing(position=0, updated=requested - timedelta(seconds=5))
        self.clock.hook = lambda: self.playing() if self.clock.value >= 0.2 else None
        epoch = await self.show.wait_for_audio(SPEAKER, URL, requested, 0, 2)
        self.assertAlmostEqual(epoch, 0.2)
        self.assertGreaterEqual(self.clock.value, 0.2)

    async def test_audio_clock_subtracts_reported_position_and_timestamp_age(self):
        requested = self.clock.utc()
        self.clock.value = 1.6
        self.playing(position=1, updated=self.clock.base + timedelta(seconds=1.2))
        epoch = await self.show.wait_for_audio(SPEAKER, URL, requested, 0, 2)
        self.assertAlmostEqual(epoch, 0.2)

    async def test_missing_position_fails_before_lights_advance(self):
        self.states[SPEAKER] = state("playing", media_content_id=URL)
        with self.assertRaisesRegex(RuntimeError, "fresh playback position"):
            await self.show.wait_for_audio(SPEAKER, URL, self.clock.utc(), 0, 2)
        self.lights.apply.assert_not_awaited()

    async def test_one_play_command_preparation_first_and_atomic_bush_window_change(self):
        order = []

        async def prepare(show_plan):
            order.append("prepare")
            self.clock.value += 2  # Controller authentication/warmup is outside soundtrack time.

        async def service(domain, action, data, blocking):
            order.append(action)
            if action == "play_media":
                self.playing()

        self.lights.prepare.side_effect = prepare
        self.hass.services.async_call.side_effect = service
        await self.show.run(plan(), {"audio_base": URL.rsplit("/", 1)[0], "brightness_pct": 2})
        self.assertIsNone(self.show.last_error)
        self.assertEqual(order[:3], ["prepare", "volume_set", "play_media"])
        self.assertEqual(order.count("play_media"), 1)
        self.assertEqual(self.lights.apply.await_count, 3)
        self.assertEqual(len(self.lights.apply.await_args_list[-1].args[0]), 2)
        self.assertAlmostEqual(self.clock.now(), 4)
        self.lights.finish.assert_awaited_once_with(keep_movie=True)
        self.lights.stop.assert_not_awaited()
        self.assertEqual(order[-2:], ["media_stop", "volume_set"])

    async def test_silent_rehearsal_never_uses_speaker(self):
        await self.show.run(plan(), {"audio": False})
        self.assertIsNone(self.show.last_error)
        self.hass.services.async_call.assert_not_awaited()
        self.lights.finish.assert_awaited_once_with(keep_movie=True)

    async def test_signed_offset_delays_or_advances_cues(self):
        for offset in (-200, 200):
            self.clock.value = 0
            self.states[SPEAKER] = state(volume_level=0.3)
            calls = []

            async def service(domain, action, data, blocking):
                if action == "play_media":
                    self.playing()

            async def apply(cues, brightness):
                calls.append(self.clock.now())

            self.hass.services.async_call.side_effect = service
            self.lights.apply.side_effect = apply
            self.lights.prepare.side_effect = lambda _: setattr(self.clock, "value", 2.0)
            await self.show.run(plan(), {"audio_base": URL.rsplit("/", 1)[0], "offset_ms": offset})
            self.assertIsNone(self.show.last_error)
            self.assertAlmostEqual(calls[1], 2.5 + offset / 1000)
            self.assertAlmostEqual(self.clock.now(), max(4.0, 4.0 + offset / 1000))

    async def test_late_operation_aborts_instead_of_bursting_stale_cues(self):
        async def slow_apply(cues, brightness):
            self.clock.value += 3

        self.lights.apply.side_effect = slow_apply
        timeline, duration = MODULE.build_timeline(plan())
        with self.assertRaisesRegex(RuntimeError, "late"):
            await self.show.run_timeline(timeline, duration, 0, 2)
        self.lights.apply.assert_awaited_once()

    async def test_prepare_failure_and_cancellation_cleanup(self):
        self.lights.prepare.side_effect = RuntimeError("Tree offline")
        with self.assertLogs("show_sync_tests", level="ERROR"):
            await self.show.run(plan(), {"audio": False})
        self.assertEqual(self.show.last_error, "Tree offline")
        self.lights.stop.assert_awaited_once()
        self.lights.apply.assert_not_awaited()
        self.lights.stop.reset_mock()
        self.lights.prepare.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.show.run(plan(), {"audio": False})
        self.lights.stop.assert_awaited_once()

    async def test_stop_after_completion_still_stops_stored_movie(self):
        self.show.task = SimpleNamespace(done=lambda: True)
        await self.show.stop()
        self.lights.stop.assert_awaited_once()
        self.assertIsNone(self.show.lights)
        await self.show.stop()
        self.lights.stop.assert_awaited_once()

    async def test_replaced_audio_is_not_stopped_or_volume_changed(self):
        self.playing(url="https://example.test/another-song.mp3")
        with self.assertRaisesRegex(RuntimeError, "replaced"):
            self.show.check_audio((SPEAKER, URL, 0, 2))
        await self.show.cleanup_audio(SPEAKER, URL, 0.3, True, True)
        self.hass.services.async_call.assert_not_awaited()

    async def test_failed_playback_restores_volume_without_stopping_old_paused_track(self):
        old_url = "https://example.test/previous-song.mp3"
        self.states[SPEAKER] = state("paused", media_content_id=old_url, volume_level=0.05)
        await self.show.cleanup_audio(SPEAKER, URL, 0.3, True, True, MODULE.media_identity(old_url))
        self.hass.services.async_call.assert_awaited_once_with(
            "media_player", "volume_set", {"entity_id": SPEAKER, "volume_level": 0.3}, blocking=True)

    async def test_cancelled_volume_request_still_restores_original_volume(self):
        async def service(domain, action, data, blocking):
            if action == "volume_set" and data["volume_level"] == 0.05:
                self.states[SPEAKER].attributes["volume_level"] = 0.05
                raise asyncio.CancelledError()
        self.hass.services.async_call.side_effect = service
        with self.assertRaises(asyncio.CancelledError):
            await self.show.run(plan(), {"audio_base": URL.rsplit("/", 1)[0]})
        self.hass.services.async_call.assert_any_await(
            "media_player", "volume_set", {"entity_id": SPEAKER, "volume_level": 0.3}, blocking=True)
        self.lights.stop.assert_awaited_once()

    async def test_cancelled_volume_request_respects_newly_started_replacement(self):
        async def service(domain, action, data, blocking):
            self.playing(url="https://example.test/user-started-song.mp3")
            raise asyncio.CancelledError()
        self.hass.services.async_call.side_effect = service
        with self.assertRaises(asyncio.CancelledError):
            await self.show.run(plan(), {"audio_base": URL.rsplit("/", 1)[0]})
        self.assertEqual(self.hass.services.async_call.await_count, 1)

    async def test_cancellation_stops_audio_before_slow_controller_cleanup(self):
        order = []
        async def service(domain, action, data, blocking):
            order.append(action)
            if action == "play_media":
                self.playing()
                raise asyncio.CancelledError()
        async def stop_lights():
            order.append("stop_lights")
        self.hass.services.async_call.side_effect = service
        self.lights.stop.side_effect = stop_lights
        with self.assertRaises(asyncio.CancelledError):
            await self.show.run(plan(), {"audio_base": URL.rsplit("/", 1)[0]})
        self.assertLess(order.index("media_stop"), order.index("stop_lights"))

    def test_sustained_seek_aborts_without_chasing_a_new_clock(self):
        self.clock.value = 3
        self.playing(position=45)
        audio = (SPEAKER, URL, 0, 140)
        self.show.check_audio(audio)
        self.clock.value += 1.1
        with self.assertRaisesRegex(RuntimeError, "position jumped"):
            self.show.check_audio(audio)

    def test_natural_sonos_end_reported_paused_retains_finale(self):
        self.clock.value = 139.5
        self.playing(position=139)
        self.states[SPEAKER].state = "paused"
        self.states[SPEAKER].attributes["media_duration"] = 140
        self.show.check_audio((SPEAKER, URL, 0, 140))
        self.assertTrue(self.show._audio_ended)

    def test_sonos_stopped_position_reset_uses_recent_confirmed_end(self):
        # Quantized Sonos position plus the polling interval may put the last
        # playing sample slightly before the final second on our local clock.
        self.clock.value = 138.85
        self.playing(position=138.85)
        self.states[SPEAKER].attributes["media_duration"] = 140
        audio = (SPEAKER, URL, 0, 140)
        self.show.check_audio(audio)
        self.clock.value = 139.1
        self.states[SPEAKER].state = "paused"
        self.states[SPEAKER].attributes.update(media_position=0, media_position_updated_at=self.clock.utc())
        self.show.check_audio(audio)
        self.clock.value = 142  # Positive light calibration can extend the final wait.
        self.show.check_audio(audio)
        self.assertTrue(self.show._audio_ended)

    def test_early_pause_does_not_look_like_natural_completion(self):
        self.clock.value = 55
        self.playing(position=55)
        self.states[SPEAKER].state = "paused"
        self.states[SPEAKER].attributes["media_duration"] = 140
        with self.assertRaisesRegex(RuntimeError, "stopped or paused"):
            self.show.check_audio((SPEAKER, URL, 0, 140))

    def test_old_paused_position_is_not_extrapolated_into_completion(self):
        self.clock.value = 139.5
        self.playing(position=20, updated=self.clock.base)
        self.states[SPEAKER].state = "paused"
        self.states[SPEAKER].attributes["media_duration"] = 140
        with self.assertRaisesRegex(RuntimeError, "stopped or paused"):
            self.show.check_audio((SPEAKER, URL, 0, 140))

    def test_replacement_or_wrong_duration_paused_track_is_not_our_completion(self):
        self.clock.value = 139.5
        self.playing(position=139, url="https://example.test/replacement.mp3")
        self.states[SPEAKER].state = "paused"
        self.states[SPEAKER].attributes["media_duration"] = 140
        with self.assertRaisesRegex(RuntimeError, "replaced"):
            self.show.check_audio((SPEAKER, URL, 0, 140))
        self.states[SPEAKER].attributes.update(media_content_id=URL, media_duration=20)
        with self.assertRaisesRegex(RuntimeError, "stopped or paused"):
            self.show.check_audio((SPEAKER, URL, 0, 140))

    def test_registry_resolution_preserves_actual_ids(self):
        bushes = SimpleNamespace(show_key="light.bush", entity_id="light.renamed_bush")
        window = SimpleNamespace(show_key="light.window", entity_id="light.renamed_window")
        self.show.ctx = {"virtual_groups": [SimpleNamespace(entities=[bushes, window]),
                                            SimpleNamespace(entities=[bushes])]}
        self.states["light.renamed_bush"] = state("off")
        self.states["light.renamed_window"] = state("off")
        data = copy.deepcopy(plan())
        entities = self.show.resolve(data)
        self.assertEqual(set(entities), {"light.renamed_bush", "light.renamed_window"})
        self.assertEqual(data["sections"][0]["cues"][1]["targets"], ["light.renamed_bush"])


if __name__ == "__main__":
    unittest.main()
