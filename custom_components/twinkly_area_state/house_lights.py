"""Bounded, capability-aware room lighting for the optional Christmas show.

Room keys stay separate from Twinkly's virtual entities. Arming expands groups
without changing them. On Go, blackout snapshots each leaf's latest state and
makes it dark; finish/stop restores the previous on/off state and the settings
of previously lit bulbs. Initially dark bulbs are restored directly to off,
without flashing them on to restore colour.
"""
import asyncio
from copy import deepcopy
import logging
import math

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)
_COLOUR_MODES = frozenset(("hs", "xy", "rgb", "rgbw", "rgbww"))
_DIMMABLE_MODES = _COLOUR_MODES | {"brightness", "color_temp", "white"}
_TRANSITION = 32

# These are Hue groups, not the similarly named Twinkly window entities. The
# Hall stairs strip is a separate Hue group member in this installation.
DEFAULT_HOUSE_ROOMS = {
    "house:drive": ("light.drive_lights",),
    "house:front_door": ("light.front_door_lights",),
    "house:lounge": ("light.lounge",),
    "house:hall": ("light.hall", "light.hall_stairs_lightstrip"),
    "house:downstairs_loo": ("light.downstairs_toilet",),
    "house:bar": ("light.bar",),
    "house:office": ("light.office",),
    "house:landing": ("light.landing",),
    "house:master_bedroom": ("light.master_bedroom",),
}


class HouseLights:
    """Own only explicitly selected room leaves until restoration completes."""

    def __init__(self, hass, room_config=None):
        self.hass = hass
        self.room_config = dict(DEFAULT_HOUSE_ROOMS)
        if room_config is not None:
            if not isinstance(room_config, dict):
                raise HomeAssistantError("house_rooms must map room keys to light entities")
            self.room_config.update(room_config)
        self.rooms = {}
        self.warnings = []
        self._snapshots = {}
        self._owned = set()
        self._last_commands = {}
        self._prepared = False
        self._active = False
        self._plan = None
        self._brightness_pct = 100.0
        self._context = Context()
        self._lock = asyncio.Lock()
        self._car_task = None
        self._car_flashed = False
        self._car_enabled = False

    def _warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)
            _LOGGER.warning("Christmas house lights: %s", text)

    @staticmethod
    def _level(value):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not 0 <= value <= 100):
            raise HomeAssistantError("House brightness must be between 0 and 100 percent")
        return float(value)

    @staticmethod
    def _modes(state):
        return set(state.attributes.get("supported_color_modes") or [])

    def _expand(self, entity_id, visiting=None):
        if not isinstance(entity_id, str) or not entity_id.startswith("light."):
            raise HomeAssistantError("House rooms must contain light entity IDs")
        visiting = set() if visiting is None else set(visiting)
        if entity_id in visiting:
            raise HomeAssistantError("Cyclic house light group: " + entity_id)
        visiting.add(entity_id)
        state = self.hass.states.get(entity_id)
        if state is None:
            self._warn("Skipping missing house light " + entity_id)
            return []
        # HA Hue room groups expose entity_id as a set. Generic light groups
        # use a list. Expand before checking group availability so usable
        # siblings remain available when one bulb is disconnected.
        members = state.attributes.get("entity_id")
        if members is not None:
            if not isinstance(members, (list, tuple, set, frozenset)):
                raise HomeAssistantError("Invalid house light group members: " + entity_id)
            leaves = []
            for member in sorted(members):
                leaves.extend(self._expand(member, visiting))
            return leaves
        if state.state in ("unknown", "unavailable"):
            self._warn("Skipping unavailable house light " + entity_id)
            return []
        if state.state not in ("on", "off"):
            self._warn("Skipping house light without an on/off state " + entity_id)
            return []
        return [entity_id]

    @staticmethod
    def _validate_cue(cue, declared):
        targets = cue.get("targets")
        if (not isinstance(targets, list) or not targets or
                any(not isinstance(key, str) or key not in declared for key in targets)):
            raise HomeAssistantError("House cues must use declared house room targets")
        if "off" in cue and type(cue["off"]) is not bool:
            raise HomeAssistantError("House cue off must be a boolean")
        level = cue.get("brightness", 255)
        if (isinstance(level, bool) or not isinstance(level, (int, float)) or
                not math.isfinite(level) or not 0 <= level <= 255):
            raise HomeAssistantError("House cue brightness must be between 0 and 255")
        colour = cue.get("rgb")
        if colour is not None and (not isinstance(colour, (list, tuple)) or
                len(colour) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in colour)):
            raise HomeAssistantError("House RGB colours must contain three integers between 0 and 255")
        if any(key in cue for key in ("effect", "stored_movie", "movie_index", "movie_name")):
            raise HomeAssistantError("House cues cannot select Twinkly effects or stored movies")

    def resolve(self, plan):
        """Read-only validation and expansion; house keys remain in the plan."""
        if self._owned or self._prepared:
            raise HomeAssistantError("House lights are already prepared")
        return self._resolve(plan)

    def _resolve(self, plan):
        declared = [key for key in plan.get("targets", [])
                    if isinstance(key, str) and key.startswith("house:")]
        room_config = dict(self.room_config)
        if "house_rooms" in plan:
            if not isinstance(plan["house_rooms"], dict):
                raise HomeAssistantError("house_rooms must map room keys to light entities")
            room_config.update(plan["house_rooms"])
        rooms = {}
        for key in declared:
            configured = room_config.get(key)
            if isinstance(configured, str):
                configured = [configured]
            if not isinstance(configured, (list, tuple)) or not configured:
                raise HomeAssistantError("No light entities configured for " + key)
            leaves = []
            for entity_id in configured:
                leaves.extend(self._expand(entity_id))
            rooms[key] = tuple(dict.fromkeys(leaves))
            if not rooms[key]:
                raise HomeAssistantError("No available house lights for " + key)
        for section in plan.get("sections", []):
            for cue in section.get("cues", []):
                targets = cue.get("targets", [])
                if any(isinstance(key, str) and key.startswith("house:") for key in targets):
                    self._validate_cue(cue, set(rooms))
        self.rooms = rooms
        return rooms

    def _transition(self, entity_id, data):
        state = self.hass.states.get(entity_id)
        if state and int(state.attributes.get("supported_features") or 0) & _TRANSITION:
            return {**data, "transition": 0}
        return data

    async def _calls(self, operations):
        """Coalesce identical commands and finish every independent light call."""
        batches = {}
        for entity_id, (service, data) in operations.items():
            key = (service, tuple(sorted((name, tuple(value) if isinstance(value, list) else value)
                                         for name, value in data.items())))
            batches.setdefault(key, []).append(entity_id)
        results = await asyncio.gather(*(
            self.hass.services.async_call(
                "light", service, {"entity_id": members, **dict(data)},
                blocking=True, context=self._context)
            for (service, data), members in batches.items()
        ), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _car_button(self, entity):
        try:
            await asyncio.wait_for(self.hass.services.async_call(
                "button", "press", {"entity_id": entity}, blocking=True), timeout=30)
        except Exception as exc:
            self._warn("Jyggy command failed: " + str(exc))

    def _start_car_button(self, entity):
        self._car_task = asyncio.create_task(self._car_button(entity))

    async def prepare(self, plan, brightness_pct=100, dark=True):
        """Arm the room selection; dark=False leaves normal house use intact."""
        async with self._lock:
            level = self._level(brightness_pct)
            self.resolve(plan)
            self._brightness_pct = level
            self._plan = deepcopy(plan)
            self._prepared = True
            self._active = False
            self._car_flashed = False
            self._car_enabled = (level == 100 and "house:drive" in self.rooms and
                                 all(self.hass.states.get(e) is not None for e in
                                     ("button.jyggy_wake", "button.jyggy_flash_lights")))
            if self._car_enabled:
                self._start_car_button("button.jyggy_wake")
            if dark:
                await self._blackout_locked()

    async def blackout(self):
        """At Go, snapshot the latest settings and darken the selected lights."""
        async with self._lock:
            await self._blackout_locked()

    async def _blackout_locked(self):
        if not self._prepared:
            raise HomeAssistantError("House lights have not been prepared")
        if self._active:
            return
        try:
            # Availability/group membership may have changed while armed. No
            # snapshot is taken until this explicit Go operation.
            self._resolve(self._plan)
            selected = set(entity for leaves in self.rooms.values() for entity in leaves)
            for entity_id in sorted(selected):
                state = self.hass.states.get(entity_id)
                if not state or state.state in ("unknown", "unavailable"):
                    self._warn("Skipping unavailable house light " + entity_id)
                    continue
                if self._brightness_pct < 100 and not self._modes(state).intersection(_DIMMABLE_MODES):
                    self._warn("Skipping undimmable house light for reduced-brightness rehearsal: " + entity_id)
                    continue
                self._snapshots[entity_id] = {
                    "state": state.state, "attributes": deepcopy(dict(state.attributes)),
                    "context_id": getattr(getattr(state, "context", None), "id", None),
                }
            # Every room must still have usable bulbs after preparation's
            # second availability check. Quiet-test exclusion alone is fine.
            for key, leaves in self.rooms.items():
                if not any(self.hass.states.get(e) and self.hass.states.get(e).state in ("on", "off") for e in leaves):
                    self._snapshots.clear()
                    raise HomeAssistantError("No available house lights for " + key)
            self._owned = set(self._snapshots)
            commands = {entity: ("turn_off", self._transition(entity, {}))
                        for entity in self._owned}
            await self._calls(commands)
            self._last_commands = commands
            self._active = True
        except BaseException:
            await self._restore_locked()
            raise

    def _command(self, entity_id, cue, brightness_pct):
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            self._warn("House light unavailable during show: " + entity_id)
            return None
        brightness = round(cue.get("brightness", 255) * brightness_pct / 100)
        if cue.get("off", False) or brightness <= 0:
            return "turn_off", self._transition(entity_id, {})
        modes = self._modes(state)
        data = {}
        if modes.intersection(_DIMMABLE_MODES):
            data["brightness"] = max(1, min(255, brightness))
        elif brightness_pct < 100:
            # This also protects a session whose level was lowered after its
            # original full-brightness prepare call.
            return "turn_off", self._transition(entity_id, {})
        if "rgb" in cue and modes.intersection(_COLOUR_MODES):
            data["rgb_color"] = tuple(cue["rgb"])
        elif "rgb" in cue and "color_temp" in modes:
            minimum = state.attributes.get("min_color_temp_kelvin") or 2000
            maximum = state.attributes.get("max_color_temp_kelvin") or 6500
            data["color_temp_kelvin"] = max(minimum, min(maximum, 3000))
        return "turn_on", self._transition(entity_id, data)

    async def apply(self, cues, brightness_pct=100):
        async with self._lock:
            if not self._prepared or not self._active:
                raise HomeAssistantError("House lights must be blacked out at Go before show cues")
            level = self._level(brightness_pct)
            try:
                operations = {}
                for cue in cues:
                    self._validate_cue(cue, set(self.rooms))
                    for key in cue["targets"]:
                        for entity_id in self.rooms[key]:
                            if entity_id not in self._owned:
                                continue
                            if self._manual_override(entity_id, self._snapshots[entity_id]):
                                self._owned.discard(entity_id)
                                self._snapshots.pop(entity_id, None)
                                self._last_commands.pop(entity_id, None)
                                self._warn("Released house light after a manual change: " + entity_id)
                                continue
                            command = self._command(entity_id, cue, level)
                            if command is not None:
                                # The final cue for a leaf wins at one timestamp.
                                operations[entity_id] = command
                # Reset-all + light-one-room cues should send only the previous
                # room off and the new room on, not rewrite every dark bulb.
                operations = {entity_id: command for entity_id, command in operations.items()
                              if not self._already_applied(entity_id, command)}
                if (self._car_enabled and not self._car_flashed and level == 100
                        and self._car_task is not None and self._car_task.done()
                        and any(e in self.rooms.get("house:drive", ()) and cmd[0] == "turn_on"
                                for e, cmd in operations.items())):
                    self._car_flashed = True
                    self._start_car_button("button.jyggy_flash_lights")
                await self._calls(operations)
                self._last_commands.update(operations)
            except BaseException:
                await self._restore_locked()
                raise

    def _already_applied(self, entity_id, command):
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        if command[0] == "turn_off":
            return state.state == "off"
        return state.state == "on" and self._last_commands.get(entity_id) == command

    def _manual_override(self, entity_id, snapshot):
        """Respect a later explicit HA user action, when HA retains context."""
        state = self.hass.states.get(entity_id)
        context = getattr(state, "context", None)
        return bool(context and getattr(context, "user_id", None) and
                    context.id not in (snapshot["context_id"], self._context.id))

    def _restore_command(self, entity_id, snapshot):
        if snapshot["state"] != "on":
            return "turn_off", self._transition(entity_id, {})
        attrs = snapshot["attributes"]
        modes = set(attrs.get("supported_color_modes") or [])
        data = {}
        if modes.intersection(_DIMMABLE_MODES) and attrs.get("brightness") is not None:
            data["brightness"] = attrs["brightness"]
        colour_mode = attrs.get("color_mode")
        attribute = {"hs": "hs_color", "xy": "xy_color", "rgb": "rgb_color",
                     "rgbw": "rgbw_color", "rgbww": "rgbww_color"}.get(colour_mode)
        if colour_mode == "color_temp" and attrs.get("color_temp_kelvin") is not None:
            data["color_temp_kelvin"] = attrs["color_temp_kelvin"]
        elif colour_mode == "color_temp" and attrs.get("color_temp") is not None:
            data["color_temp"] = attrs["color_temp"]
        elif attribute and attrs.get(attribute) is not None:
            data[attribute] = tuple(attrs[attribute])
        if attrs.get("effect") is not None and attrs.get("effect") in (attrs.get("effect_list") or []):
            data["effect"] = attrs["effect"]
        return "turn_on", self._transition(entity_id, data)

    async def _restore_locked(self, raise_errors=False):
        self._prepared = False
        self._active = False
        self._plan = None
        self._car_enabled = False
        if self._car_task is not None:
            self._car_task.cancel()
            await asyncio.gather(self._car_task, return_exceptions=True)
            self._car_task = None
        operations = {}
        for entity_id in sorted(self._owned):
            snapshot = self._snapshots[entity_id]
            if self._manual_override(entity_id, snapshot):
                self._owned.discard(entity_id)
                self._snapshots.pop(entity_id, None)
                self._last_commands.pop(entity_id, None)
                continue
            operations[entity_id] = self._restore_command(entity_id, snapshot)
        # Restore independently, so a failed bulb does not leave its siblings
        # dark. Keep failed snapshots for a subsequent stop/retry.
        results = await asyncio.gather(*(
            self._calls({entity_id: command}) for entity_id, command in operations.items()
        ), return_exceptions=True)
        errors = []
        for entity_id, result in zip(operations, results):
            if isinstance(result, BaseException):
                _LOGGER.error("Could not restore Christmas house light %s: %s", entity_id, result)
                errors.append(result)
            else:
                self._owned.discard(entity_id)
                self._snapshots.pop(entity_id, None)
                self._last_commands.pop(entity_id, None)
        if errors and raise_errors:
            raise errors[0]

    async def stop(self):
        """Restore once, finishing restoration even if the stop caller cancels."""
        async with self._lock:
            cleanup = asyncio.create_task(self._restore_locked(raise_errors=True))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    async def finish(self):
        await self.stop()
