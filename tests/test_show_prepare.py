"""Offline prepare/start lifecycle tests, with no HA or device connections."""
import ast
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qsl, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state"
SPEAKER = "media_player.test_sonos"
BASE = "https://example.test/local/christmas"
URL = BASE + "/show.mp3"


def definitions(path, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    exec(compile(ast.Module(body=[node for node in tree.body
                                 if not isinstance(node, (ast.Import, ast.ImportFrom))],
                            type_ignores=[]), str(path), "exec"), namespace)
    return SimpleNamespace(**namespace)


def plan():
    return {
        "speaker": SPEAKER,
        "soundtrack": {"file": "show.mp3", "duration": 2},
        "targets": ["light.front"],
        "sections": [{"id": "opening", "duration": 2, "cues": [
            {"at": 0, "targets": ["light.front"], "off": True},
            {"at": .5, "targets": ["light.front"], "rgb": [255, 0, 0]},
        ]}],
    }


class PrepareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 0.0
        self.base = datetime.now(timezone.utc)
        self.watch_tick = asyncio.Event()
        self.tasks = []
        self.calls = []
        self.service_hook = None
        self.states = {
            SPEAKER: SimpleNamespace(state="paused", attributes={
                "volume_level": .27, "group_members": [SPEAKER],
                "media_content_id": "https://example.test/previous.mp3",
            }),
            "light.front": SimpleNamespace(state="off", attributes={}),
        }
        self.lights = SimpleNamespace(
            prepare=AsyncMock(side_effect=self.prepare_lights),
            apply=AsyncMock(), finish=AsyncMock(), stop=AsyncMock(),
            check_ready=Mock(),
        )
        self.hass = SimpleNamespace(
            states=SimpleNamespace(get=self.states.get),
            services=SimpleNamespace(async_call=self.service),
            async_add_executor_job=AsyncMock(side_effect=lambda job: copy.deepcopy(plan())),
            async_create_background_task=self.background,
        )
        namespace = {
            "__file__": str(ROOT / "show.py"), "__name__": "prepare_test_show",
            "asyncio": asyncio, "sleep": self.sleep, "datetime": datetime,
            "timezone": timezone, "json": json, "logging": logging, "math": math,
            "Path": Path, "monotonic": lambda: self.now,
            "parse_qsl": parse_qsl, "unquote": unquote, "urlsplit": urlsplit,
            "HomeAssistantError": RuntimeError,
            "ShowLights": Mock(return_value=self.lights), "HouseLights": Mock(),
        }
        self.module = definitions(ROOT / "show.py", namespace)
        original_position = namespace["media_position"]
        namespace["media_position"] = lambda state, now=None: original_position(
            state, now or self.utc())
        self.namespace = namespace
        entity = SimpleNamespace(show_key="light.front", entity_id="light.front")
        self.show = self.module.Show(self.hass, {
            "virtual_groups": [SimpleNamespace(entities=[entity])],
        })

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def utc(self):
        return self.base + timedelta(seconds=self.now)

    def background(self, coroutine, name):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    async def sleep(self, seconds):
        if asyncio.current_task().get_name() == "Christmas ready timeout":
            await self.watch_tick.wait()
            self.watch_tick.clear()
        else:
            self.now += seconds
            await asyncio.sleep(0)

    async def prepare_lights(self, _plan):
        # Slow controller warmup is deliberately outside both playback clocks.
        self.now += 3

    async def service(self, domain, action, data, blocking):
        self.assertTrue(blocking)
        self.calls.append((domain, action, dict(data)))
        speaker = self.states[SPEAKER]
        attrs = speaker.attributes
        if action == "volume_set":
            attrs["volume_level"] = data["volume_level"]
        elif action == "play_media":
            speaker.state = "playing"
            attrs.update(media_content_id=data["media_content_id"], media_position=0,
                         media_position_updated_at=self.utc(), media_duration=2)
        elif action == "media_play":
            speaker.state = "playing"
            attrs["media_position_updated_at"] = self.utc()
        elif action == "media_pause":
            speaker.state = "paused"
            attrs["media_position_updated_at"] = self.utc()
        elif action == "media_seek":
            attrs["media_position"] = data["seek_position"]
            # A successful paused Sonos seek need not update this timestamp.
        elif action == "media_stop":
            speaker.state = "paused"
        if self.service_hook is not None:
            await self.service_hook(domain, action, data)

    @staticmethod
    def call(**data):
        return SimpleNamespace(data=data)

    async def prepare(self, *, countdown=False, **options):
        action = self.show.prepare_countdown if countdown else self.show.prepare
        await action(self.call(audio_base=BASE, **options))
        await self.show.task
        self.assertEqual(self.show.status, "ready", self.show.last_error)
        self.assertIsNotNone(self.show.prepared)

    def actions(self):
        return [action for _, action, _ in self.calls]

    async def tick_watch(self):
        watch = self.show._ready_task
        self.watch_tick.set()
        await watch

    async def test_prepare_loads_silently_pauses_rewinds_and_does_not_run_cues(self):
        await self.prepare(brightness_pct=37, house_brightness_pct=100, volume=.12)
        self.assertEqual(self.actions(), ["volume_set", "play_media", "media_pause",
                                         "media_seek", "volume_set"])
        self.assertEqual(self.calls[0][2]["volume_level"], 0)
        self.assertEqual(self.calls[-1][2]["volume_level"], .12)
        self.assertEqual(self.states[SPEAKER].state, "paused")
        self.assertEqual(self.states[SPEAKER].attributes["media_position"], 0)
        self.lights.apply.assert_not_awaited()
        self.lights.finish.assert_not_awaited()
        self.lights.stop.assert_not_awaited()
        native_plan = self.lights.prepare.await_args.args[0]
        self.assertEqual(native_plan["prepare_hold_seconds"], self.module.PREPARED_TTL)
        self.assertEqual(self.show.prepared["audio_cleanup"]["volume"], .27)

    async def test_start_only_resumes_prepared_track_and_inherits_settings(self):
        await self.prepare(brightness_pct=37, volume=.12, offset_ms=150)
        self.now += 45
        calls_before = len(self.calls)
        await self.show.start(self.call())
        await self.show.task
        self.assertIsNone(self.show.last_error)
        self.assertEqual(self.show.status, "finished")
        self.assertIsNone(self.show.prepared)
        self.assertIsNone(self.show._ready_task)
        self.assertEqual(self.calls[calls_before][1], "media_play")
        self.assertEqual(self.actions().count("play_media"), 1)
        self.assertEqual(self.actions().count("media_play"), 1)
        self.lights.prepare.assert_awaited_once()
        self.assertEqual(self.hass.async_add_executor_job.await_count, 1)
        self.assertTrue(all(call.args[1] == 37 for call in self.lights.apply.await_args_list))
        self.lights.finish.assert_awaited_once_with(keep_movie=True)
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)

    async def test_house_variant_can_start_without_repeating_variant_or_options(self):
        await self.prepare(variant="house_chase", house_brightness_pct=100)
        self.assertEqual(self.show.prepared["variant"], "house_chase")
        await self.show.start(self.call())
        await self.show.task
        self.assertIsNone(self.show.last_error)
        self.assertEqual(self.show._house_brightness, 100)

    async def test_completion_stays_busy_until_audio_and_house_cleanup_finish(self):
        await self.prepare()
        audio_entered, audio_release = asyncio.Event(), asyncio.Event()
        house_entered, house_release = asyncio.Event(), asyncio.Event()

        async def hold_audio(domain, action, data):
            if action == "media_stop":
                audio_entered.set()
                await audio_release.wait()

        async def hold_house():
            house_entered.set()
            await house_release.wait()

        self.service_hook = hold_audio
        self.show.house_lights = SimpleNamespace(blackout=AsyncMock(), stop=hold_house, disarm_car=Mock())
        await self.show.start_prepared(self.call())
        await asyncio.wait_for(audio_entered.wait(), 1)
        self.assertEqual(self.show.status, "stopping")
        self.show.house_lights.disarm_car.assert_called()
        audio_release.set()
        await asyncio.wait_for(house_entered.wait(), 1)
        self.assertEqual(self.show.status, "stopping")
        house_release.set()
        await self.show.task
        self.assertEqual(self.show.status, "finished")
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)

    async def test_reprepare_is_busy_while_releasing_the_previous_session(self):
        await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_stop(domain, action, data):
            if action == "media_stop":
                entered.set()
                await release.wait()

        self.service_hook = hold_stop
        request = asyncio.create_task(self.show.prepare(self.call(audio_base=BASE)))
        self.tasks.append(request)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual(self.show.status, "preparing")
        release.set()
        await request
        await self.show.task
        self.assertEqual(self.show.status, "ready")

    async def test_wrong_programme_variant_or_settings_do_not_launch(self):
        await self.prepare(variant="house_chase", brightness_pct=12)
        actions_before = self.actions()
        with self.assertRaisesRegex(RuntimeError, "different programme"):
            await self.show.start_countdown(self.call())
        with self.assertRaisesRegex(RuntimeError, "different show variant"):
            await self.show.start(self.call(variant="original"))
        with self.assertRaisesRegex(RuntimeError, "settings changed"):
            await self.show.start(self.call(brightness_pct=13))
        self.assertEqual(self.actions(), actions_before)
        self.assertEqual(self.show.status, "ready")
        self.lights.apply.assert_not_awaited()

    async def test_button_start_refuses_to_cold_start_without_preparation(self):
        with self.assertRaisesRegex(RuntimeError, "No programme is ready"):
            await self.show.start_prepared(self.call())
        self.hass.async_add_executor_job.assert_not_awaited()
        self.assertEqual(self.calls, [])
        self.lights.prepare.assert_not_awaited()

    async def test_muted_speaker_fails_before_controller_or_audio_changes(self):
        self.states[SPEAKER].attributes["is_volume_muted"] = True
        with self.assertRaisesRegex(RuntimeError, "Unmute"):
            await self.show.prepare(self.call(audio_base=BASE))
        self.assertEqual(self.calls, [])
        self.lights.prepare.assert_not_awaited()

    async def test_button_start_uses_whichever_programme_is_prepared(self):
        await self.prepare(countdown=True)
        await self.show.start_prepared(self.call())
        await self.show.task
        self.assertEqual(self.show.status, "finished")
        self.assertEqual(self.show.kind, "countdown")
        self.assertEqual(self.actions().count("play_media"), 1)
        self.assertEqual(self.actions().count("media_play"), 1)

    async def test_countdown_requires_countdown_start_and_resumes_ready_track(self):
        await self.prepare(countdown=True)
        self.assertEqual(self.show.prepared["kind"], "countdown")
        with self.assertRaisesRegex(RuntimeError, "different programme"):
            await self.show.start(self.call())
        await self.show.start_countdown(self.call())
        await self.show.task
        self.assertEqual(self.actions().count("play_media"), 1)
        self.assertEqual(self.actions().count("media_play"), 1)
        self.assertEqual(self.show.status, "finished")

    async def test_paused_zero_does_not_age_forward_while_waiting(self):
        await self.prepare()
        self.now += 300
        self.show._check_paused(self.show.prepared)
        self.assertEqual(self.states[SPEAKER].attributes["media_position"], 0)
        for value in (1, -1, True, float("nan"), float("inf"), None):
            self.states[SPEAKER].attributes["media_position"] = value
            with self.assertRaisesRegex(RuntimeError, "not at the beginning"):
                self.show._check_paused(self.show.prepared)

    async def test_stop_ready_stops_our_audio_restores_volume_and_lights(self):
        await self.prepare(volume=.08)
        await self.show.stop()
        self.assertEqual(self.actions()[-2:], ["media_stop", "volume_set"])
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)
        self.assertEqual(self.show.status, "idle")
        self.assertIsNone(self.show.prepared)
        self.assertIsNone(self.show._ready_task)
        self.assertIsNone(self.show.lights)
        self.lights.stop.assert_awaited_once()
        self.lights.apply.assert_not_awaited()

    async def test_stop_immediately_after_go_before_run_starts_still_restores_audio(self):
        await self.prepare(volume=.08)
        await self.show.start_prepared(self.call())
        await self.show.stop()
        self.assertNotIn("media_play", self.actions())
        self.assertEqual(self.actions()[-2:], ["media_stop", "volume_set"])
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)
        self.assertIsNone(self.show._pending_audio_cleanup)
        self.assertIsNone(self.show.prepared)
        self.assertEqual(self.show.status, "idle")

    async def test_failure_before_audio_still_cleans_lights(self):
        self.lights.prepare.side_effect = RuntimeError("Controller missing")
        await self.show.prepare(self.call(audio_base=BASE))
        with self.assertLogs("prepare_test_show", level="ERROR"):
            await self.show.task
        self.assertEqual(self.show.status, "error")
        self.assertEqual(self.show.last_error, "Controller missing")
        self.assertIsNone(self.show.prepared)
        self.assertEqual(self.calls, [])
        self.lights.stop.assert_awaited_once()

    async def test_prepare_failure_stays_busy_until_its_cleanup_finishes(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_cleanup(domain, action, data):
            if action == "media_seek":
                raise RuntimeError("Seek failed")
            if action == "media_stop":
                entered.set()
                await release.wait()

        self.service_hook = hold_cleanup
        with self.assertLogs("prepare_test_show", level="ERROR"):
            await self.show.prepare(self.call(audio_base=BASE))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(self.show.status, "stopping")
            self.assertEqual(self.show.last_error, "Seek failed")
            release.set()
            await self.show.task
        self.assertEqual(self.show.status, "error")
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)

    async def test_unconfirmed_seek_never_becomes_ready_or_unmutes_playback(self):
        async def stale_seek(domain, action, data):
            if action == "media_seek":
                self.states[SPEAKER].attributes["media_position"] = 2

        self.service_hook = stale_seek
        await self.show.prepare(self.call(audio_base=BASE, volume=.12))
        with self.assertLogs("prepare_test_show", level="ERROR"):
            await self.show.task
        self.assertEqual(self.show.status, "error")
        self.assertIn("paused at zero", self.show.last_error)
        self.assertIsNone(self.show.prepared)
        self.assertFalse(any(action == "volume_set" and data["volume_level"] == .12
                             for _, action, data in self.calls))
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)
        self.lights.apply.assert_not_awaited()
        self.lights.stop.assert_awaited_once()

    async def test_replacement_during_preparation_is_not_sought_or_revolumed(self):
        for replaced_after in ("media_pause", "media_seek"):
            with self.subTest(replaced_after=replaced_after):
                self.calls.clear()
                self.lights.stop.reset_mock()
                self.states[SPEAKER] = SimpleNamespace(state="paused", attributes={
                    "volume_level": .27, "group_members": [SPEAKER],
                    "media_content_id": "https://example.test/previous.mp3",
                })
                replaced_index = None

                async def replace(domain, action, data):
                    nonlocal replaced_index
                    if action == replaced_after:
                        replaced_index = len(self.calls)
                        self.states[SPEAKER].state = "playing"
                        self.states[SPEAKER].attributes.update(
                            media_content_id="https://example.test/user-song.mp3",
                            media_position=40, volume_level=.4)

                self.service_hook = replace
                await self.show.prepare(self.call(audio_base=BASE, volume=.12))
                with self.assertLogs("prepare_test_show", level="ERROR"):
                    await self.show.task
                self.assertIsNotNone(replaced_index)
                self.assertEqual(self.calls[replaced_index:], [])
                self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .4)
                self.assertIsNone(self.show.prepared)
                self.assertEqual(self.show.status, "error")
                self.lights.stop.assert_awaited_once()
                self.show.lights = None

    async def test_failed_stop_of_silent_preload_keeps_zero_volume_and_retry_state(self):
        async def fail_transport(domain, action, data, blocking):
            if action in ("media_pause", "media_stop"):
                self.calls.append((domain, action, dict(data)))
                raise RuntimeError("Cannot " + action)
            await self.service(domain, action, data, blocking)

        self.hass.services.async_call = fail_transport
        await self.show.prepare(self.call(audio_base=BASE))
        with self.assertLogs("prepare_test_show", level="ERROR"):
            await self.show.task
        self.assertEqual(self.states[SPEAKER].state, "playing")
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], 0)
        self.assertEqual(self.show.status, "error")
        self.assertIn("stop", self.show.last_error.lower())
        self.assertIsNotNone(self.show._pending_audio_cleanup)
        self.assertIsNone(self.show.prepared)
        sensor_module = definitions(ROOT / "sensor.py", {
            "SensorEntity": type("SensorEntity", (), {}), "DeviceInfo": dict,
            "DOMAIN": "twinkly_area_state",
        })
        sensor = sensor_module.ChristmasShowStatus("test", self.show)
        self.assertIs(sensor.extra_state_attributes["audio_cleanup_pending"], True)
        self.hass.services.async_call = self.service
        await self.show.stop()
        self.assertEqual(self.actions()[-2:], ["media_stop", "volume_set"])
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)
        self.assertIsNone(self.show._pending_audio_cleanup)
        self.assertIs(sensor.extra_state_attributes["audio_cleanup_pending"], False)
        self.assertEqual(self.show.status, "idle")

    async def test_replacement_arriving_during_stop_keeps_its_own_volume(self):
        await self.prepare()
        before = len(self.calls)

        async def replace_during_stop(domain, action, data):
            if action == "media_stop":
                self.states[SPEAKER].state = "playing"
                self.states[SPEAKER].attributes.update(
                    media_content_id="https://example.test/new-song.mp3", volume_level=.6)

        self.service_hook = replace_during_stop
        await self.show.stop()
        self.assertEqual([action for _, action, _ in self.calls[before:]], ["media_stop"])
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .6)
        self.assertIsNone(self.show._pending_audio_cleanup)

    async def test_cancelled_speaker_mutation_restores_original_volume(self):
        for cancelled_action in ("volume_set", "play_media", "media_pause", "media_seek"):
            with self.subTest(cancelled_action=cancelled_action):
                self.calls.clear()
                self.lights.stop.reset_mock()
                self.states[SPEAKER] = SimpleNamespace(state="paused", attributes={
                    "volume_level": .27, "group_members": [SPEAKER],
                    "media_content_id": "https://example.test/previous.mp3",
                })
                fired = False

                async def cancel_once(domain, action, data):
                    nonlocal fired
                    if action == cancelled_action and not fired:
                        fired = True
                        raise asyncio.CancelledError()

                self.service_hook = cancel_once
                await self.show.prepare(self.call(audio_base=BASE))
                with self.assertRaises(asyncio.CancelledError):
                    await self.show.task
                self.assertIsNone(self.show.prepared)
                self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)
                self.lights.stop.assert_awaited_once()
                self.lights.apply.assert_not_awaited()
                # Old cancelled session does not remain installed for the next subtest.
                self.show.lights = None

    async def test_replacement_audio_while_ready_is_not_stopped_or_revolumed(self):
        await self.prepare()
        before = len(self.calls)
        self.states[SPEAKER].state = "playing"
        self.states[SPEAKER].attributes.update(
            media_content_id="https://example.test/user-song.mp3", volume_level=.4)
        await self.tick_watch()
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .4)
        self.assertEqual(self.show.status, "error")
        self.assertIsNone(self.show.prepared)
        self.lights.stop.assert_awaited_once()

    async def test_ready_expiry_releases_session_and_restores_volume(self):
        await self.prepare()
        self.now += self.module.PREPARED_TTL
        await self.tick_watch()
        self.assertEqual(self.show.status, "expired")
        self.assertIsNone(self.show.prepared)
        self.assertEqual(self.actions()[-2:], ["media_stop", "volume_set"])
        self.assertEqual(self.states[SPEAKER].attributes["volume_level"], .27)
        self.lights.stop.assert_awaited_once()

    async def test_expiry_stays_busy_until_audio_and_lights_are_released(self):
        await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_stop(domain, action, data):
            if action == "media_stop":
                entered.set()
                await release.wait()

        self.service_hook = hold_stop
        self.now += self.module.PREPARED_TTL
        watch = self.show._ready_task
        self.watch_tick.set()
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual(self.show.status, "stopping")
        release.set()
        await watch
        self.assertEqual(self.show.status, "expired")
        self.lights.stop.assert_awaited_once()

    async def test_ready_expiry_cleanup_failure_never_leaves_false_ready_status(self):
        await self.prepare()
        self.lights.stop.side_effect = RuntimeError("Controller unreachable")
        self.now += self.module.PREPARED_TTL
        with self.assertLogs("prepare_test_show", level="ERROR"):
            await self.tick_watch()
        self.assertIn(self.show.status, ("expired", "error"))
        self.assertIsNone(self.show.prepared)
        self.assertIn("Controller unreachable", self.show.last_error)
        self.assertIs(self.show.lights, self.lights)
        self.lights.stop.side_effect = None
        await self.show.stop()
        self.assertEqual(self.show.status, "idle")
        self.assertIsNone(self.show.lights)

    async def test_silent_prepare_and_start_never_touch_speaker(self):
        await self.prepare(audio=False)
        self.assertEqual(self.calls, [])
        await self.show.start(self.call())
        await self.show.task
        self.assertEqual(self.calls, [])
        self.assertEqual(self.show.status, "finished")
        self.lights.prepare.assert_awaited_once()

    async def test_house_lights_stay_normal_until_go_and_use_independent_brightness(self):
        mixed = plan()
        mixed["targets"].append("house:lounge")
        mixed["sections"][0]["cues"].append({
            "at": .5, "targets": ["house:lounge"], "brightness": 255,
            "rgb": [255, 180, 20],
        })
        self.hass.async_add_executor_job.side_effect = lambda job: copy.deepcopy(mixed)

        async def blackout():
            self.calls.append(("house", "blackout", {}))

        house = SimpleNamespace(resolve=Mock(), prepare=AsyncMock(),
                                blackout=AsyncMock(side_effect=blackout),
                                apply=AsyncMock(), stop=AsyncMock(), disarm_car=Mock())
        self.namespace["HouseLights"].return_value = house
        await self.prepare(variant="house_chase", brightness_pct=2, house_brightness_pct=100)
        house.blackout.assert_not_awaited()
        self.assertIs(house.prepare.await_args.kwargs.get("dark"), False)
        await self.show.start_prepared(self.call())
        await self.show.task
        self.assertIsNone(self.show.last_error)
        house.blackout.assert_awaited_once()
        self.assertLess(self.actions().index("blackout"), self.actions().index("media_play"))
        house.apply.assert_awaited_once()
        self.assertEqual(house.apply.await_args.args[1], 100)
        self.assertTrue(all(call.args[1] == 2 for call in self.lights.apply.await_args_list))
        house.stop.assert_awaited_once()

    async def test_stop_can_cancel_controller_preparation(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_prepare(_plan):
            entered.set()
            await release.wait()

        self.lights.prepare.side_effect = blocked_prepare
        await self.show.prepare(self.call(audio_base=BASE))
        await entered.wait()
        with self.assertRaisesRegex(RuntimeError, "running or preparing"):
            await self.show.prepare(self.call(audio_base=BASE))
        await self.show.stop()
        self.assertTrue(self.show.task.done())
        self.assertEqual(self.show.status, "idle")
        self.assertIsNone(self.show.prepared)
        self.assertIsNone(self.show.lights)
        self.assertEqual(self.calls, [])

    async def test_go_resumes_audio_without_waiting_for_house_bridge_acknowledgement(self):
        await self.prepare()
        playing = asyncio.Event()

        async def slow_blackout():
            # An acknowledgement can be slow; Go must already have sent Play.
            await playing.wait()

        async def on_service(domain, action, data):
            if action == "media_play":
                playing.set()

        self.show.house_lights = SimpleNamespace(
            blackout=AsyncMock(side_effect=slow_blackout), stop=AsyncMock(), disarm_car=Mock())
        self.service_hook = on_service
        await self.show.start_prepared(self.call())
        await asyncio.wait_for(self.show.task, 1)
        self.assertTrue(playing.is_set())
        self.assertIsNone(self.show.last_error)
        self.assertEqual(self.actions().count("play_media"), 1)
        self.assertEqual(self.actions().count("media_play"), 1)

    async def test_failed_ready_stream_cannot_start_audio(self):
        await self.prepare()
        before = len(self.calls)
        self.lights.check_ready.side_effect = RuntimeError("Prepared lighting stream changed")
        with self.assertRaisesRegex(RuntimeError, "stream changed"):
            await self.show.start(self.call())
        self.assertEqual(len(self.calls), before)
        self.lights.apply.assert_not_awaited()


class ReadyLightingTests(unittest.TestCase):
    """Exercise the real no-I/O readiness guard against lost transport state."""

    def setUp(self):
        tree = ast.parse((ROOT / "show_lights.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "ShowLights")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == "check_ready")
        namespace = {"HomeAssistantError": RuntimeError,
                     "time": SimpleNamespace(monotonic=lambda: 10)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "check_ready", "exec"), namespace)
        self.check = namespace["check_ready"]
        self.group = Mock(active=True, error=False, hold_idle=True, native_effect=None,
                          task=Mock(done=lambda: False), members=[{"mac": "a"}],
                          expiry={"a": 1000}, any_on=Mock(return_value=False))
        self.session = SimpleNamespace(_prepared=True, groups=[self.group], _owned={self.group})

    def test_healthy_held_stream_is_ready_without_io(self):
        self.check(self.session, 140)
        self.group.assert_not_called()

    def test_lost_ownership_error_stream_or_near_expiry_requires_prepare(self):
        for name, value in (("active", False), ("error", True), ("hold_idle", False),
                            ("native_effect", "Movie: User"), ("task", None)):
            with self.subTest(field=name):
                old = getattr(self.group, name)
                setattr(self.group, name, value)
                with self.assertRaisesRegex(RuntimeError, "stream changed"):
                    self.check(self.session, 140)
                setattr(self.group, name, old)
        self.group.expiry["a"] = 160  # now + show + 15-second margin is 165.
        with self.assertRaisesRegex(RuntimeError, "session expired"):
            self.check(self.session, 140)
        self.session._owned.clear()
        with self.assertRaisesRegex(RuntimeError, "session was released"):
            self.check(self.session, 140)

    def test_manual_light_turn_on_invalidates_dark_readiness(self):
        self.group.any_on.return_value = True
        with self.assertRaisesRegex(RuntimeError, "prepare again"):
            self.check(self.session, 140)


if __name__ == "__main__":
    unittest.main()
