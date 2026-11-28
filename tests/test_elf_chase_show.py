"""Run V3 through the real scene engine with simulated HA, car and Sonos I/O."""
import asyncio
from types import SimpleNamespace
import unittest

from test_house_show_integration import HouseSimulation, MOVIES


class ElfChaseShowTests(unittest.IsolatedAsyncioTestCase):
    def make_sim(self, *, unavailable_audio=False):
        sim = HouseSimulation(variant="elf_chase_v3")
        if unavailable_audio:
            # Keep the fail-closed regression without disabling the released
            # score on disk. Normal simulations use the shipped plan directly.
            def load_unavailable_fixture(loader):
                programme = loader()
                programme["soundtrack"]["available"] = False
                return programme
            sim.hass.async_add_executor_job.side_effect = load_unavailable_fixture
        sim.states["button.jyggy_wake"] = SimpleNamespace(state="unknown", attributes={})
        sim.states["button.jyggy_flash_lights"] = SimpleNamespace(state="unknown", attributes={})
        self.addAsyncCleanup(sim.close)
        return sim

    def assert_house_restored(self, sim):
        for entity_id, before in sim.house_initial.items():
            after = sim.states[entity_id]
            self.assertEqual(after.state, before.state, entity_id)
            if before.state == "on":
                for key in ("brightness", "xy_color", "color_temp_kelvin", "color_mode"):
                    if key in before.attributes:
                        self.assertEqual(after.attributes[key], before.attributes[key], (entity_id, key))

    async def test_v3_prepares_its_own_track_and_runs_both_car_flashes_with_refresh_wake(self):
        sim = self.make_sim()
        self.assertIs(sim.plan["soundtrack"]["available"], True)
        await sim.prepare()
        self.assertEqual(sim.show.status, "ready", sim.show.last_error)
        self.assertEqual(sim.show.prepared["variant"], "elf_chase_v3")
        self.assertEqual(sim.show.prepared["url"],
                         "https://example.test/local/christmas/christmas_show_elf_chase_v3.mp3")
        self.assertGreater(sim.show.prepared["duration"], 140)
        self.assertEqual(sim.states[sim.speaker].state, "paused")
        self.assertEqual(sim.house_events, [])
        await sim.show.house_lights._car_task
        self.assertEqual([data["entity_id"] for _, action, data in sim.events if action == "press"],
                         ["button.jyggy_wake"])
        self.assert_house_restored(sim)

        started_at = sim.now
        await sim.go()
        self.assertIsNone(sim.show.last_error)
        self.assertEqual(sim.show.status, "finished")
        self.assertAlmostEqual(sim.now - started_at, sim.plan["soundtrack"]["duration"])
        self.assertEqual([action for _, action, _ in sim.events if action in ("play_media", "media_play")],
                         ["play_media", "media_play"])
        self.assertEqual([data["entity_id"] for _, action, data in sim.events if action == "press"],
                         ["button.jyggy_wake", "button.jyggy_wake", "button.jyggy_flash_lights",
                          "button.jyggy_wake", "button.jyggy_flash_lights"])

        # Refresh wake is 30 seconds before the farewell flash. The original
        # first driveway flash and the final native movie both stay intact.
        car_at = [at - started_at for at, action, data in sim.events
                  if action == "press" and data["entity_id"] == "button.jyggy_flash_lights"]
        timeline, _ = sim.controller.build_timeline(sim.plan)
        driveway_at = next(at for at, cues in timeline
                           if any("house:drive" in cue["targets"] and not cue.get("off") for cue in cues))
        self.assertAlmostEqual(car_at[0], driveway_at)
        self.assertAlmostEqual(car_at[1], 204.4)
        wakes = [at - started_at for at, action, data in sim.events
                 if action == "press" and data["entity_id"] == "button.jyggy_wake"]
        self.assertAlmostEqual(wakes[1], 28)
        self.assertAlmostEqual(car_at[0] - wakes[1], 30)
        self.assertAlmostEqual(wakes[2], 174.4)
        self.assertAlmostEqual(car_at[1] - wakes[2], 30)
        selected = []
        for _, _, movie, _ in sim.snapshots:
            if movie and (not selected or movie != selected[-1]):
                selected.append(movie)
        self.assertEqual(selected, ["Movie: " + name for name in MOVIES])
        self.assertEqual(sim.group.native_effect, "Movie: Christmas Spirit")
        self.assert_house_restored(sim)
        self.assertEqual(sim.states[sim.speaker].attributes["volume_level"], 0.3)

    async def test_stop_after_first_flash_prevents_later_wake_and_farewell_flash(self):
        sim = self.make_sim()
        first_flash = asyncio.Event()
        service = sim.hass.services.async_call

        async def observe(domain, action, data, **kwargs):
            await service(domain, action, data, **kwargs)
            if domain == "button" and data["entity_id"] == "button.jyggy_flash_lights":
                first_flash.set()

        sim.hass.services.async_call = observe
        await sim.prepare()
        started_at = sim.now
        await sim.show.start_prepared(SimpleNamespace(data={}))
        await first_flash.wait()
        await sim.show.stop()
        self.assertLess(sim.now - started_at, 174.4)
        self.assertEqual(sim.show.status, "idle")
        self.assertEqual([data["entity_id"] for _, action, data in sim.events if action == "press"],
                         ["button.jyggy_wake", "button.jyggy_wake", "button.jyggy_flash_lights"])
        self.assert_house_restored(sim)

    async def test_failed_refresh_wake_keeps_full_show_running_but_omits_farewell_flash(self):
        sim = self.make_sim()
        service = sim.hass.services.async_call
        wake_count = 0

        async def fail_refresh(domain, action, data, **kwargs):
            nonlocal wake_count
            await service(domain, action, data, **kwargs)
            if domain == "button" and data["entity_id"] == "button.jyggy_wake":
                wake_count += 1
                if wake_count == 3:
                    raise RuntimeError("Simulated refresh wake failure")

        sim.hass.services.async_call = fail_refresh
        await sim.prepare()
        await sim.go()
        self.assertEqual(sim.show.status, "finished", sim.show.last_error)
        self.assertEqual([data["entity_id"] for _, action, data in sim.events if action == "press"],
                         ["button.jyggy_wake", "button.jyggy_wake", "button.jyggy_flash_lights", "button.jyggy_wake"])
        self.assertEqual(sim.group.native_effect, "Movie: Christmas Spirit")
        self.assert_house_restored(sim)

    async def test_show_stop_disarms_queued_or_started_car_work_before_cleanup_awaits(self):
        refresh = {"targets": ["house:drive"], "off": True, "car_wake": "v3_farewell"}
        farewell = {"targets": ["house:drive"], "off": True, "car_flash": "v3_farewell"}
        for cue in (refresh, farewell):
            for parent_started in (False, True):
                with self.subTest(cue=cue, parent_started=parent_started):
                    sim = self.make_sim()
                    await sim.prepare()
                    house = sim.show.house_lights
                    await house._car_task
                    await house.blackout()
                    if cue is farewell:
                        await house.apply([refresh])
                        await house._car_task
                    await house.apply([cue])
                    if parent_started:
                        # A started command may already have reached HA.
                        # Stop must prevent new dispatch, not undo that call.
                        await asyncio.sleep(0)
                    before = [data["entity_id"] for _, action, data in sim.events if action == "press"]
                    await sim.show.stop()
                    self.assertEqual(sim.show.status, "idle")
                    self.assertEqual([data["entity_id"] for _, action, data in sim.events if action == "press"],
                                     before)
                    self.assert_house_restored(sim)

    async def test_show_stop_cancels_pending_refresh_before_audio_cleanup_finishes(self):
        sim = self.make_sim()
        service = sim.hass.services.async_call
        refresh_started, refresh_cancelled = asyncio.Event(), asyncio.Event()
        audio_cleanup_started, release_audio_cleanup = asyncio.Event(), asyncio.Event()
        wake_count = 0

        async def hold_refresh_and_audio(domain, action, data, **kwargs):
            nonlocal wake_count
            await service(domain, action, data, **kwargs)
            if domain == "button" and data["entity_id"] == "button.jyggy_wake":
                wake_count += 1
                if wake_count == 2:
                    refresh_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        refresh_cancelled.set()
            elif action == "media_stop":
                audio_cleanup_started.set()
                await release_audio_cleanup.wait()

        sim.hass.services.async_call = hold_refresh_and_audio
        await sim.prepare()
        house = sim.show.house_lights
        await house._car_task
        await house.blackout()
        await house.apply([{"targets": ["house:drive"], "off": True, "car_wake": "v3_farewell"}])
        await refresh_started.wait()
        stop = asyncio.create_task(sim.show.stop())
        await audio_cleanup_started.wait()
        await asyncio.wait_for(refresh_cancelled.wait(), 1)
        self.assertFalse(house._car_enabled)
        self.assertFalse(stop.done())
        release_audio_cleanup.set()
        await stop
        self.assertEqual([data["entity_id"] for _, action, data in sim.events if action == "press"],
                         ["button.jyggy_wake", "button.jyggy_wake"])
        self.assert_house_restored(sim)

    async def test_v3_ready_session_rejects_a_different_selected_version(self):
        sim = self.make_sim()
        await sim.prepare()
        before = list(sim.events)
        for variant in ("original", "house_chase"):
            with self.assertRaisesRegex(RuntimeError, "different show variant"):
                await sim.show.start(SimpleNamespace(data={"variant": variant}))
        with self.assertRaisesRegex(RuntimeError, "different programme"):
            await sim.show.start_countdown(SimpleNamespace(data={}))
        self.assertEqual(sim.show.status, "ready")
        self.assertEqual(sim.events, before)
        self.assertEqual(sim.house_events, [])

    async def test_unavailable_soundtrack_prepare_and_cold_start_refuse_before_any_device_writes(self):
        for action in ("prepare", "start"):
            with self.subTest(action=action):
                sim = self.make_sim(unavailable_audio=True)
                with self.assertRaisesRegex(RuntimeError, "soundtrack has not been released"):
                    await getattr(sim.show, action)(SimpleNamespace(data={
                        "variant": "elf_chase_v3", "audio": True,
                        "audio_base": "https://example.test/local/christmas",
                    }))
                self.assertEqual(sim.show.status, "idle")
                self.assertIsNone(sim.show.prepared)
                self.assertIsNone(sim.show.task)
                self.assertEqual(sim.events, [])
                self.assertEqual(sim.house_events, [])
                sim.group.validate.assert_not_awaited()
                self.assert_house_restored(sim)

    async def test_shipped_v3_allows_explicit_silent_lighting_rehearsal(self):
        sim = self.make_sim()
        await sim.show.prepare(SimpleNamespace(data={
            "variant": "elf_chase_v3", "audio": False, "brightness_pct": 2,
        }))
        await sim.show.task
        self.assertEqual(sim.show.status, "ready", sim.show.last_error)
        self.assertIsNone(sim.show.prepared["url"])
        await sim.go()
        self.assertEqual(sim.show.status, "finished", sim.show.last_error)
        self.assertFalse(any(action in ("play_media", "media_play", "media_stop", "volume_set")
                             for _, action, _ in sim.events))
        self.assert_house_restored(sim)


if __name__ == "__main__":
    unittest.main()
