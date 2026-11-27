"""Offline room-light ownership tests; no Home Assistant or hardware needed."""
import ast
import asyncio
from copy import deepcopy
import logging
import math
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4


class Context:
    def __init__(self, user_id=None, id=None):
        self.id = id or uuid4().hex
        self.user_id = user_id


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/twinkly_area_state/house_lights.py"
TREE = ast.parse(SOURCE.read_text())
NAMESPACE = {"asyncio": asyncio, "deepcopy": deepcopy, "logging": logging,
             "math": math, "Context": Context, "HomeAssistantError": RuntimeError,
             "__name__": "house_light_tests"}
exec(compile(ast.Module(body=[node for node in TREE.body
                             if not isinstance(node, (ast.Import, ast.ImportFrom))],
                        type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)
HouseLights = NAMESPACE["HouseLights"]


def state(on="off", modes=("color_temp", "xy"), **attrs):
    return SimpleNamespace(state=on, attributes={"supported_color_modes": list(modes), **attrs},
                           context=Context())


def plan(rooms=None, cues=None):
    rooms = rooms or {"house:lounge": ["light.room"]}
    return {"targets": list(rooms), "house_rooms": rooms,
            "sections": [{"duration": 20, "cues": cues or [
                {"at": 0, "targets": list(rooms), "rgb": [255, 0, 0], "brightness": 255}]}]}


CAR_DRIVE = {"targets": ["house:drive"]}
CAR_OFF = {"targets": ["house:drive"], "off": True}
CAR_REFRESH = {**CAR_OFF, "car_wake": "v3_farewell"}
CAR_FAREWELL = {**CAR_OFF, "car_flash": "v3_farewell"}


def car_plan():
    return {**plan({"house:drive": ["light.dim"]},
                   [{"at": i, **cue} for i, cue in enumerate(
                       (CAR_DRIVE, CAR_REFRESH, CAR_FAREWELL))]),
            "variant": "elf_chase_v3"}


class HouseLightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = {
            "light.room": state(entity_id={"light.colour", "light.white", "light.dim", "light.switch"}),
            "light.colour": state("on", brightness=87, color_mode="xy", xy_color=(0.35, 0.4),
                                  supported_features=32),
            "light.white": state("off", ("color_temp",), brightness=120, color_temp_kelvin=2700),
            "light.dim": state("on", ("brightness",), brightness=123),
            "light.switch": state("off", ("onoff",)),
        }
        self.events = []
        self.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get),
                                    services=SimpleNamespace(async_call=AsyncMock(side_effect=self.service)))
        self.lights = HouseLights(self.hass)

    async def service(self, domain, service, data, blocking, context):
        self.events.append((service, deepcopy(data)))
        for entity_id in data["entity_id"]:
            old = self.states[entity_id]
            attrs = {**old.attributes, **{k: v for k, v in data.items()
                                         if k not in ("entity_id", "transition")}}
            self.states[entity_id] = SimpleNamespace(
                state="on" if service == "turn_on" else "off", attributes=attrs, context=context)

    def calls_for(self, entity_id):
        return [(service, data) for service, data in self.events if entity_id in data["entity_id"]]

    async def prepare(self, programme=None, level=100):
        await self.lights.prepare(programme or plan(), level)
        self.events.clear()
        self.hass.services.async_call.reset_mock()

    def car_buttons(self, on_button=None):
        self.states["button.jyggy_wake"] = state()
        self.states["button.jyggy_flash_lights"] = state()
        buttons = []

        async def service(domain, action, data, blocking, context=None):
            if domain == "button":
                buttons.append(data["entity_id"])
                if on_button:
                    await on_button(data["entity_id"], buttons)
            else:
                await self.service(domain, action, data, blocking, context)

        self.hass.services.async_call.side_effect = service
        return buttons

    async def arm_car(self, level=100):
        await self.lights.prepare(car_plan(), level, dark=False)
        if self.lights._car_task is not None:
            await self.lights._car_task
        await self.lights.blackout()

    async def first_car_flash(self):
        await self.lights.apply([CAR_DRIVE])
        if self.lights._car_task is not None:
            await self.lights._car_task
        await self.lights.apply([CAR_OFF])

    async def test_resolution_is_read_only_and_expands_set_groups(self):
        rooms = self.lights.resolve(plan())
        self.assertEqual(set(rooms["house:lounge"]), {"light.colour", "light.white", "light.dim", "light.switch"})
        self.hass.services.async_call.assert_not_awaited()
        self.assertEqual(self.states["light.colour"].state, "on")

    async def test_preparation_darkens_leaves_and_deduplicates_overlaps(self):
        programme = plan({"house:lounge": ["light.room", "light.colour"], "house:hall": ["light.colour"]})
        await self.lights.prepare(programme)
        for entity_id in ("light.colour", "light.white", "light.dim", "light.switch"):
            self.assertEqual(len(self.calls_for(entity_id)), 1)
            self.assertEqual(self.states[entity_id].state, "off")
        self.assertFalse(self.calls_for("light.room"))

    async def test_armed_prepare_keeps_normal_house_lights_and_takes_no_snapshot(self):
        await self.lights.prepare(plan(), dark=False)
        self.hass.services.async_call.assert_not_awaited()
        self.assertEqual(self.states["light.colour"].state, "on")
        self.assertEqual(self.states["light.dim"].state, "on")
        self.assertFalse(self.lights._snapshots)
        self.assertFalse(self.lights._owned)
        with self.assertRaisesRegex(RuntimeError, "at Go"):
            await self.lights.apply([{"targets": ["house:lounge"]}])

    async def test_go_snapshots_latest_settings_from_armed_wait(self):
        await self.lights.prepare(plan(), dark=False)
        # Normal house use between Prepare and Go must be the restore point.
        self.states["light.colour"] = state("on", brightness=206, color_mode="xy", xy_color=(0.2, 0.3))
        self.states["light.colour"].context = Context(user_id="person")
        self.states["light.dim"].state = "off"
        await self.lights.blackout()
        self.assertEqual(self.states["light.colour"].state, "off")
        self.assertEqual(self.lights._snapshots["light.colour"]["attributes"]["brightness"], 206)
        await self.lights.apply([{"targets": ["house:lounge"], "rgb": [255, 0, 0]}])
        await self.lights.stop()
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 206)
        self.assertEqual(self.states["light.colour"].attributes["xy_color"], (0.2, 0.3))
        self.assertEqual(self.states["light.dim"].state, "off")

    async def test_stopping_armed_show_makes_no_house_writes(self):
        await self.lights.prepare(plan(), dark=False)
        self.states["light.colour"].attributes["brightness"] = 201
        await self.lights.stop()
        self.hass.services.async_call.assert_not_awaited()
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 201)
        self.assertFalse(self.lights._prepared)

    async def test_repeated_blackout_does_not_overwrite_original_snapshot(self):
        await self.lights.prepare(plan(), dark=False)
        await self.lights.blackout()
        self.events.clear()
        await self.lights.blackout()
        self.assertFalse(self.events)
        await self.lights.stop()
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 87)
        self.assertEqual(self.states["light.colour"].state, "on")

    async def test_go_rechecks_availability_before_darkening_any_room(self):
        await self.lights.prepare(plan(), dark=False)
        for leaf in self.states["light.room"].attributes["entity_id"]:
            self.states[leaf].state = "unavailable"
        with self.assertRaisesRegex(RuntimeError, "No available house lights"):
            await self.lights.blackout()
        self.hass.services.async_call.assert_not_awaited()
        self.assertFalse(self.lights._owned)

    async def test_drive_and_door_defaults_select_exactly_the_four_confirmed_bulbs(self):
        self.states.update({
            "light.drive_lights": state(entity_id=["light.drive_1", "light.drive_2"]),
            "light.front_door_lights": state(entity_id=["light.front_door_1", "light.front_door_2"]),
            "light.drive_1": state(modes=("color_temp",)),
            "light.drive_2": state(modes=("color_temp",)),
            "light.front_door_1": state(modes=("color_temp",)),
            "light.front_door_2": state(modes=("color_temp",)),
            "light.side_outside_light": state("unavailable"),
        })
        programme = {"targets": ["house:drive", "house:front_door"], "sections": []}
        await self.lights.prepare(programme, dark=False)
        await self.lights.blackout()
        self.assertEqual(self.lights._owned,
                         {"light.drive_1", "light.drive_2", "light.front_door_1", "light.front_door_2"})
        self.assertFalse(self.calls_for("light.side_outside_light"))

    async def test_capabilities_split_colour_white_dimmer_and_switch_commands(self):
        await self.prepare()
        await self.lights.apply([{"targets": ["house:lounge"], "rgb": [255, 0, 20], "brightness": 255}], 100)
        colour = self.calls_for("light.colour")[-1][1]
        white = self.calls_for("light.white")[-1][1]
        dim = self.calls_for("light.dim")[-1][1]
        switch = self.calls_for("light.switch")[-1][1]
        self.assertEqual(colour["rgb_color"], (255, 0, 20))
        self.assertEqual(colour["brightness"], 255)
        self.assertEqual(colour["transition"], 0)
        self.assertEqual(white["color_temp_kelvin"], 3000)
        self.assertNotIn("rgb_color", white)
        self.assertEqual(dim, {"entity_id": ["light.dim"], "brightness": 255})
        self.assertEqual(switch, {"entity_id": ["light.switch"]})

    async def test_unchanged_default_house_brightness_is_full_output(self):
        await self.prepare()
        await self.lights.apply([{"targets": ["house:lounge"], "brightness": 255}])
        self.assertEqual(self.calls_for("light.colour")[-1][1]["brightness"], 255)

    async def test_reduced_brightness_does_not_touch_onoff_lamp(self):
        self.states["light.switch"].state = "on"
        await self.lights.prepare(plan(), 5)
        self.assertEqual(self.states["light.switch"].state, "on")
        self.assertFalse(self.calls_for("light.switch"))
        await self.lights.apply([{"targets": ["house:lounge"], "brightness": 255}], 5)
        self.assertEqual(self.calls_for("light.colour")[-1][1]["brightness"], 13)
        self.assertFalse(self.calls_for("light.switch"))
        await self.lights.stop()
        self.assertFalse(self.calls_for("light.switch"))
        self.assertTrue(any("undimmable" in message for message in self.lights.warnings))

    async def test_lowering_level_after_prepare_does_not_light_undimmable_bulb(self):
        await self.prepare()
        await self.lights.apply([{"targets": ["house:lounge"], "brightness": 255}], 2)
        self.assertFalse(self.calls_for("light.switch"))
        self.assertEqual(self.states["light.switch"].state, "off")

    async def test_last_same_time_cue_wins_without_intermediate_flash(self):
        await self.prepare()
        await self.lights.apply([
            {"targets": ["house:lounge"], "off": True},
            {"targets": ["house:lounge"], "rgb": [0, 255, 0], "brightness": 255},
        ])
        self.assertEqual(len(self.calls_for("light.colour")), 1)
        self.assertEqual(self.calls_for("light.colour")[0][0], "turn_on")

    async def test_finish_restores_on_settings_and_off_without_flash(self):
        await self.prepare()
        await self.lights.apply([{"targets": ["house:lounge"], "rgb": [255, 0, 0]}])
        self.events.clear()
        await self.lights.finish()
        self.assertEqual(self.states["light.colour"].state, "on")
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 87)
        self.assertEqual(self.states["light.colour"].attributes["xy_color"], (0.35, 0.4))
        self.assertEqual(self.states["light.dim"].attributes["brightness"], 123)
        self.assertEqual(self.calls_for("light.white")[0][0], "turn_off")
        self.assertEqual(len(self.calls_for("light.white")), 1)
        self.assertEqual(self.states["light.switch"].state, "off")
        self.events.clear()
        await self.lights.stop()
        self.assertFalse(self.events)

    async def test_missing_or_unavailable_leaf_is_skipped_but_empty_room_fails_preflight(self):
        self.states["light.colour"].state = "unavailable"
        del self.states["light.white"]
        await self.lights.prepare(plan())
        self.assertFalse(self.calls_for("light.colour"))
        self.assertFalse(self.calls_for("light.white"))
        self.assertEqual(len(self.lights.warnings), 2)
        await self.lights.stop()
        self.events.clear()
        with self.assertRaisesRegex(RuntimeError, "No available house lights"):
            await self.lights.prepare(plan({"house:lounge": ["light.colour"]}))
        self.assertFalse(self.events)

    async def test_cyclic_group_rejected_before_any_writes(self):
        self.states["light.room"].attributes["entity_id"].add("light.room")
        with self.assertRaisesRegex(RuntimeError, "Cyclic"):
            await self.lights.prepare(plan())
        self.hass.services.async_call.assert_not_awaited()

    async def test_malformed_cues_are_rejected_during_read_only_preflight(self):
        for changes in ({"rgb": [999, 0, 0]}, {"brightness": float("nan")},
                        {"off": "true"}, {"stored_movie": True},
                        {"targets": ["house:lounge", "light.twinkly_lounge"]}):
            with self.subTest(changes=changes):
                with self.assertRaises(RuntimeError):
                    self.lights.resolve(plan(cues=[{"targets": ["house:lounge"], **changes}]))
        self.hass.services.async_call.assert_not_awaited()

    async def test_prepare_failure_restores_lights_even_after_partial_darkening(self):
        failed = False
        async def service(domain, action, data, blocking, context):
            nonlocal failed
            await self.service(domain, action, data, blocking, context)
            if not failed:
                failed = True
                raise RuntimeError("bridge failure")
        self.hass.services.async_call.side_effect = service
        with self.assertRaisesRegex(RuntimeError, "bridge failure"):
            await self.lights.prepare(plan())
        self.assertEqual(self.states["light.colour"].state, "on")
        self.assertEqual(self.states["light.dim"].state, "on")
        self.assertEqual(self.states["light.white"].state, "off")
        self.assertFalse(self.lights._owned)

    async def test_prepare_cancellation_restores_lights(self):
        entered = asyncio.Event()
        first = True
        async def service(domain, action, data, blocking, context):
            nonlocal first
            await self.service(domain, action, data, blocking, context)
            if first:
                first = False
                entered.set()
                await asyncio.Event().wait()
        self.hass.services.async_call.side_effect = service
        task = asyncio.create_task(self.lights.prepare(plan()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.states["light.colour"].state, "on")
        self.assertEqual(self.states["light.dim"].state, "on")
        self.assertFalse(self.lights._owned)

    async def test_apply_failure_restores_whole_session(self):
        await self.prepare()
        failed = False
        async def service(domain, action, data, blocking, context):
            nonlocal failed
            await self.service(domain, action, data, blocking, context)
            if not failed:
                failed = True
                raise RuntimeError("bulb lost")
        self.hass.services.async_call.side_effect = service
        with self.assertRaisesRegex(RuntimeError, "bulb lost"):
            await self.lights.apply([{"targets": ["house:lounge"], "rgb": [0, 255, 0]}])
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 87)
        self.assertEqual(self.states["light.white"].state, "off")
        self.assertFalse(self.lights._owned)

    async def test_stop_cancellation_finishes_restoration_before_return(self):
        await self.prepare()
        entered = asyncio.Event()
        release = asyncio.Event()
        async def service(domain, action, data, blocking, context):
            entered.set()
            await release.wait()
            await self.service(domain, action, data, blocking, context)
        self.hass.services.async_call.side_effect = service
        task = asyncio.create_task(self.lights.stop())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.states["light.colour"].state, "on")
        self.assertFalse(self.lights._owned)

    async def test_failed_restoration_keeps_only_failed_bulb_for_retry(self):
        await self.prepare()
        async def service(domain, action, data, blocking, context):
            if "light.colour" in data["entity_id"]:
                raise RuntimeError("offline during restore")
            await self.service(domain, action, data, blocking, context)
        self.hass.services.async_call.side_effect = service
        with self.assertRaisesRegex(RuntimeError, "offline during restore"):
            await self.lights.stop()
        self.assertEqual(self.lights._owned, {"light.colour"})
        self.assertEqual(self.states["light.dim"].state, "on")
        self.hass.services.async_call.side_effect = self.service
        await self.lights.stop()
        self.assertEqual(self.states["light.colour"].state, "on")
        self.assertFalse(self.lights._owned)

    async def test_later_explicit_manual_light_choice_is_not_overwritten_on_stop(self):
        await self.prepare()
        self.states["light.colour"] = SimpleNamespace(
            state="on", attributes={"supported_color_modes": ["xy"], "brightness": 32},
            context=Context(user_id="person"))
        await self.lights.stop()
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 32)
        self.assertFalse(self.calls_for("light.colour"))

    async def test_manual_override_is_released_from_remaining_chase_cues(self):
        await self.prepare()
        self.states["light.colour"] = SimpleNamespace(
            state="on", attributes={"supported_color_modes": ["xy"], "brightness": 32},
            context=Context(user_id="person"))
        await self.lights.apply([{"targets": ["house:lounge"], "rgb": [255, 0, 0]}])
        await self.lights.stop()
        self.assertFalse(self.calls_for("light.colour"))
        self.assertEqual(self.states["light.colour"].attributes["brightness"], 32)

    async def test_repeated_scene_does_not_resend_already_applied_commands(self):
        await self.prepare()
        cues = [{"targets": ["house:lounge"], "off": True},
                {"targets": ["house:lounge"], "rgb": [0, 255, 0]}]
        await self.lights.apply(cues)
        self.events.clear()
        await self.lights.apply(cues)
        self.assertFalse(self.events)

    async def test_shipped_variant_resolves_and_runs_all_house_cues(self):
        programme = json.loads(SOURCE.with_name("christmas_show_house_chase.json").read_text())
        for room, configured in programme["house_rooms"].items():
            if isinstance(configured, str):
                configured = [configured]
            for index, group_id in enumerate(configured):
                leaf = "light.test_" + room.replace("house:", "") + str(index)
                self.states[group_id] = state(entity_id={leaf})
                self.states[leaf] = state()
        await self.prepare(programme)
        scenes = {}
        offset = 0
        for section in programme["sections"]:
            for cue in section["cues"]:
                if all(target.startswith("house:") for target in cue["targets"]):
                    scenes.setdefault(offset + cue["at"], []).append(cue)
            offset += section["duration"]
        for _, cues in sorted(scenes.items()):
            await self.lights.apply(cues)
        lit_leaves = {entity for service, data in self.events if service == "turn_on" for entity in data["entity_id"]}
        self.assertEqual(lit_leaves, set(self.lights._snapshots))
        self.assertTrue(all(self.states[leaf].state == "off" for leaf in lit_leaves))
        await self.lights.finish()

    async def test_jyggy_wakes_on_prepare_and_flashes_once_on_drive_cue(self):
        self.states["button.jyggy_wake"] = state()
        self.states["button.jyggy_flash_lights"] = state()
        buttons = []
        async def service(domain, action, data, blocking, context=None):
            if domain == "button":
                buttons.append(data["entity_id"])
            else:
                await self.service(domain, action, data, blocking, context)
        self.hass.services.async_call.side_effect = service
        programme = plan({"house:drive": ["light.dim"]})
        await self.lights.prepare(programme, dark=False)
        await self.lights._car_task
        self.assertEqual(buttons, ["button.jyggy_wake"])
        await self.lights.blackout()
        await self.lights.apply([{"targets": ["house:drive"], "off": True}])
        await self.lights.apply([{"targets": ["house:drive"]}])
        await self.lights._car_task
        await self.lights.apply([{"targets": ["house:drive"], "off": True}])
        await self.lights.apply([{"targets": ["house:drive"]}])
        await self.lights.stop()
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights"])
        await self.lights.prepare(programme, 5, dark=False)
        await self.lights.blackout()
        await self.lights.apply([{"targets": ["house:drive"]}], 5)
        await self.lights.stop()
        self.assertEqual(len(buttons), 2)

    async def test_v3_refresh_and_farewell_are_each_once_per_session_without_light_writes(self):
        buttons = self.car_buttons()
        for _ in range(2):
            await self.arm_car()
            await self.first_car_flash()
            self.events.clear()
            await self.lights.apply([CAR_REFRESH, CAR_REFRESH])
            await self.lights._car_task
            await self.lights.apply([CAR_REFRESH])
            await self.lights.apply([CAR_FAREWELL, CAR_FAREWELL])
            await self.lights._car_task
            await self.lights.apply([CAR_FAREWELL])
            self.assertEqual(self.events, [])
            # Repeated driveway scenes still cannot retrigger the first flash.
            await self.lights.apply([CAR_DRIVE])
            await self.lights.stop()
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights",
                                  "button.jyggy_wake", "button.jyggy_flash_lights"] * 2)

    async def test_farewell_needs_its_refresh_wake_not_the_successful_prepare_wake(self):
        buttons = self.car_buttons()
        await self.arm_car()
        await self.first_car_flash()
        await self.lights.apply([CAR_FAREWELL])
        await self.lights.stop()
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights"])

    async def test_failed_refresh_wake_skips_farewell_and_duplicates_do_not_retry(self):
        async def fail_refresh(entity, buttons):
            if entity == "button.jyggy_wake" and buttons.count(entity) == 2:
                raise RuntimeError("refresh unavailable")

        buttons = self.car_buttons(fail_refresh)
        await self.arm_car()
        await self.first_car_flash()
        await self.lights.apply([CAR_REFRESH])
        await self.lights._car_task
        await self.lights.apply([CAR_REFRESH, CAR_FAREWELL, CAR_FAREWELL])
        await self.lights.stop()
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights", "button.jyggy_wake"])
        self.assertTrue(any("refresh unavailable" in warning for warning in self.lights.warnings))

    async def test_pending_refresh_skips_farewell_without_late_replay_when_wake_finishes(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def hold_refresh(entity, buttons):
            if entity == "button.jyggy_wake" and buttons.count(entity) == 2:
                started.set()
                await release.wait()

        buttons = self.car_buttons(hold_refresh)
        await self.arm_car()
        await self.first_car_flash()
        await self.lights.apply([CAR_REFRESH])
        await started.wait()
        await self.lights.apply([CAR_FAREWELL])
        release.set()
        await self.lights._car_task
        await self.lights.apply([CAR_FAREWELL])
        await self.lights.stop()
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights", "button.jyggy_wake"])

    async def test_failed_prepare_and_refresh_wakes_never_flash(self):
        async def fail_wake(entity, buttons):
            if entity == "button.jyggy_wake":
                raise RuntimeError("car asleep")

        buttons = self.car_buttons(fail_wake)
        await self.arm_car()
        await self.first_car_flash()
        await self.lights.apply([CAR_REFRESH])
        await self.lights._car_task
        await self.lights.apply([CAR_FAREWELL])
        await self.lights.stop()
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_wake"])

    async def test_missing_buttons_and_reduced_house_levels_disable_both_flashes(self):
        for missing in ("button.jyggy_wake", "button.jyggy_flash_lights", None):
            with self.subTest(missing=missing):
                buttons = self.car_buttons()
                if missing:
                    del self.states[missing]
                level = 100 if missing else 5
                await self.arm_car(level)
                await self.lights.apply([CAR_DRIVE], level)
                await self.lights.apply([CAR_REFRESH], level)
                await self.lights.apply([CAR_FAREWELL], level)
                await self.lights.stop()
                self.assertEqual(buttons, [])

    async def test_disappearing_buttons_or_lowered_level_skip_refresh_and_farewell(self):
        for missing in ("button.jyggy_wake", "button.jyggy_flash_lights", None):
            with self.subTest(missing=missing):
                buttons = self.car_buttons()
                await self.arm_car()
                await self.first_car_flash()
                if missing:
                    del self.states[missing]
                level = 100 if missing else 5
                await self.lights.apply([CAR_REFRESH], level)
                await self.lights.apply([CAR_FAREWELL], level)
                await self.lights.stop()
                self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights"])

    async def test_stop_cancels_queued_refresh_or_farewell_before_dispatch(self):
        for cue in (CAR_REFRESH, CAR_FAREWELL):
            with self.subTest(cue=cue):
                buttons = self.car_buttons()
                await self.arm_car()
                await self.first_car_flash()
                if cue is CAR_FAREWELL:
                    await self.lights.apply([CAR_REFRESH])
                    await self.lights._car_task
                before = list(buttons)
                await self.lights.apply([cue])
                await self.lights.stop()
                self.assertEqual(buttons, before)
                self.assertIsNone(self.lights._car_task)
                with self.assertRaisesRegex(RuntimeError, "at Go"):
                    await self.lights.apply([CAR_FAREWELL])

    async def test_stop_cancels_pending_refresh_and_never_flashes_later(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def pending_refresh(entity, buttons):
            if entity == "button.jyggy_wake" and buttons.count(entity) == 2:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        buttons = self.car_buttons(pending_refresh)
        await self.arm_car()
        await self.first_car_flash()
        await self.lights.apply([CAR_REFRESH])
        await started.wait()
        await self.lights.stop()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(buttons, ["button.jyggy_wake", "button.jyggy_flash_lights", "button.jyggy_wake"])

    async def test_explicit_car_cues_reject_other_variants_names_or_light_changes(self):
        for variant, cue in [("house_chase", CAR_REFRESH),
                             ("elf_chase_v3", {**CAR_REFRESH, "car_wake": "other"}),
                             ("elf_chase_v3", {**CAR_FAREWELL, "off": False}),
                             ("elf_chase_v3", {**CAR_REFRESH, "car_flash": "v3_farewell"})]:
            with self.subTest(variant=variant, cue=cue):
                programme = {**plan({"house:drive": ["light.dim"]}, [cue]), "variant": variant}
                with self.assertRaisesRegex(RuntimeError, "car cues"):
                    await self.lights.prepare(programme)
                self.hass.services.async_call.assert_not_awaited()

    async def test_no_house_targets_is_noop(self):
        await self.lights.prepare({"targets": ["front"], "sections": []})
        await self.lights.finish()
        self.hass.services.async_call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
