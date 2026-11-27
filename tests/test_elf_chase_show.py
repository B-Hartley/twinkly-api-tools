"""Run V3 through the real scene engine with simulated HA, car and Sonos I/O."""
from types import SimpleNamespace
import unittest

from test_house_show_integration import HouseSimulation, MOVIES


class ElfChaseShowTests(unittest.IsolatedAsyncioTestCase):
    def make_sim(self, *, released_audio=False):
        sim = HouseSimulation(variant="elf_chase_v3")
        if released_audio:
            # Exercise the future complete audio path without changing the
            # shipped plan's production gate or pretending an MP3 exists.
            def load_released_fixture(loader):
                programme = loader()
                programme["soundtrack"]["available"] = True
                return programme
            sim.hass.async_add_executor_job.side_effect = load_released_fixture
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

    async def test_v3_prepares_its_own_track_and_runs_full_chase_with_one_car_flash(self):
        sim = self.make_sim(released_audio=True)
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
                         ["button.jyggy_wake", "button.jyggy_flash_lights"])

        # The single flash follows the driveway scene, before the indoor chase.
        car_at = next(at - started_at for at, action, data in sim.events
                      if action == "press" and data["entity_id"] == "button.jyggy_flash_lights")
        timeline, _ = sim.controller.build_timeline(sim.plan)
        driveway_at = next(at for at, cues in timeline
                           if any("house:drive" in cue["targets"] and not cue.get("off") for cue in cues))
        self.assertAlmostEqual(car_at, driveway_at)
        selected = []
        for _, _, movie, _ in sim.snapshots:
            if movie and (not selected or movie != selected[-1]):
                selected.append(movie)
        self.assertEqual(selected, ["Movie: " + name for name in MOVIES])
        self.assertEqual(sim.group.native_effect, "Movie: Christmas Spirit")
        self.assert_house_restored(sim)
        self.assertEqual(sim.states[sim.speaker].attributes["volume_level"], 0.3)

    async def test_v3_ready_session_rejects_a_different_selected_version(self):
        sim = self.make_sim(released_audio=True)
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

    async def test_shipped_v3_audio_prepare_and_cold_start_refuse_before_any_device_writes(self):
        for action in ("prepare", "start"):
            with self.subTest(action=action):
                sim = self.make_sim()
                self.assertIs(sim.plan["soundtrack"]["available"], False)
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
