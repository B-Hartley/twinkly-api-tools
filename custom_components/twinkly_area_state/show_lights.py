"""Prepared, scene-based playback for the explicitly started Christmas show.

The show owns only its participating groups. Controller discovery, authentication,
movie lookup and the real-time warm-up all happen before the soundtrack starts.
Normal light services retain their existing last-light-off behaviour.
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import logging
import time

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)
_MODE = "/xled/v1/led/mode"
_CHASES = ("Window Chase", "House and Garden Chase")


async def _complete(awaitables):
    """Finish every independent operation, then report the first failure."""
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


class ShowLights:
    """A bounded lighting session; all public operations are cancellation safe."""

    def __init__(self, hass, ctx, entities):
        self.hass = hass
        self.ctx = ctx
        self.entities = entities
        self.groups = []
        self._owned = set()
        self._retained = {}
        self._movies = {}
        self._paths = {}
        self._prepared = False

    @staticmethod
    def _bindings(entity):
        if hasattr(entity, "targets"):
            return [(group, members, None, True) for group, members in entity.targets]
        group = entity.group
        if hasattr(entity, "member"):
            return [(group, [entity.member], getattr(entity, "index", None), False)]
        return [(group, group.members, None, False)]

    @asynccontextmanager
    async def _locked(self):
        # Integration groups normally share one lock. Deduplication also makes
        # the helper safe if independent locks are introduced in the future.
        locks = {id(group.lock): group.lock for group in self.groups}
        async with AsyncExitStack() as stack:
            for key in sorted(locks):
                await stack.enter_async_context(locks[key])
            yield

    async def prepare(self, plan):
        if self._prepared:
            raise HomeAssistantError("Show lights are already prepared")
        for target in plan["targets"]:
            if target not in self.entities:
                raise HomeAssistantError("Show light not registered: " + target)
            for group, _, _, _ in self._bindings(self.entities[target]):
                if group not in self.groups:
                    self.groups.append(group)
        movie_groups = set()
        for section in plan["sections"]:
            for cue in section["cues"]:
                self._check_cue(cue, set(plan["targets"]), section["duration"])
                if cue.get("stored_movie") or str(cue.get("effect", "")).startswith("Movie: "):
                    for target in cue["targets"]:
                        bindings = self._bindings(self.entities[target])
                        if len(bindings) != 1 or bindings[0][2] is not None or len(bindings[0][1]) != len(bindings[0][0].members):
                            raise HomeAssistantError("Stored movies require a complete physical group")
                        movie_groups.add(bindings[0][0])
        async with self._locked():
            try:
                # No playback writes until every group has passed preflight.
                await _complete([group.validate() for group in self.groups])
                for group in self.groups:
                    modes = await _complete([
                        group.call(member, _MODE, token=group.tokens[member["mac"]])
                        for member in group.members
                    ])
                    for mode in modes:
                        current = mode.get("mode")
                        if current == "rt" and group.active:
                            continue
                        if current not in ("off", "movie", "playlist", "color", "effect"):
                            raise HomeAssistantError("Stop external streaming before starting the Christmas show")
                    group.previous = modes[0].get("mode", "off")
                for group in movie_groups:
                    await group.refresh_movies()
                    self._movies[group] = {label: ids.copy() for label, ids in group.movies.items()}
                    if not self._movies[group] and plan.get("requires_stored_movies", False):
                        raise HomeAssistantError("The finale needs at least one stored movie present on every Front controller")
                    if self._movies[group]:
                        paths = await _complete([group.movie_current_path(member) for member in group.members])
                        self._paths[group] = dict(zip((m["mac"] for m in group.members), paths))
                self._validate_scenes(plan)
                duration = float(plan.get("soundtrack", {}).get("duration", sum(s["duration"] for s in plan["sections"])))
                for group in self.groups:
                    await _complete([
                        group.login(member) for member in group.members
                        if group.expiry.get(member["mac"], 0) < time.monotonic() + duration + 15
                    ])
                # From this point cancellation must turn off every group whose
                # mode or brightness we may have touched, even partial failures.
                self._owned.update(self.groups)
                for group in self.groups:
                    self._detach(group)
                    self._black(group)
                    group.native_effect = None
                    group.hold_idle = True
                # A previous native movie may be deliberately dim. Extinguish
                # it before restoring the native output level for RGB frames;
                # otherwise preparation can produce a full-brightness flash.
                await _complete([group.mode("off") for group in self.groups])
                await _complete([group.native_level(100) for group in self.groups])
                await _complete([group.mode("rt") for group in self.groups])
                await _complete([group.send() for group in self.groups])
                for group in self.groups:
                    group.active = True
                    group.error = False
                    group.task = self.hass.async_create_background_task(group.run(), "Twinkly Christmas show frames")
                    group.notify()
                self._prepared = True
            except BaseException:
                await self._stop_locked()
                raise

    @staticmethod
    def _detach(group):
        group.active = False
        if group.task:
            group.task.cancel()
            group.task = None

    @staticmethod
    def _black(group):
        group.state = {key: (False, value[1], value[2]) for key, value in group.state.items()}
        group.pane_overrides.clear()
        group.pane_states.clear()
        group.combined_selected.clear()

    @staticmethod
    def _draft(group):
        return {
            "state": group.state.copy(), "effects": group.effects.copy(),
            "pane_states": group.pane_states.copy(), "pane_effects": group.pane_effects.copy(),
            "pane_overrides": group.pane_overrides.copy(),
            "combined_selected": group.combined_selected.copy(),
            "chase_started": group.chase_started,
        }

    @staticmethod
    def _check_cue(cue, targets, duration):
        if not isinstance(cue.get("targets"), list) or not cue["targets"] or any(not isinstance(target, str) or target not in targets for target in cue["targets"]):
            raise HomeAssistantError("Every show cue must use declared lighting targets")
        at = cue.get("at")
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not 0 <= at <= duration:
            raise HomeAssistantError("Show cue time must be within its section")
        brightness = cue.get("brightness", 255)
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not 0 <= brightness <= 255:
            raise HomeAssistantError("Show brightness must be between 0 and 255")
        for key in ("rgb", "fallback_rgb"):
            color = cue.get(key)
            if key in cue and (not isinstance(color, (list, tuple)) or len(color) != 3 or any(type(value) is not int or not 0 <= value <= 255 for value in color)):
                raise HomeAssistantError("Show RGB colours must contain three integers between 0 and 255")
        for key in ("effect", "fallback_effect"):
            if key in cue and (not isinstance(cue[key], str) or not cue[key]):
                raise HomeAssistantError("Show effects must have a nonempty name")
        if "movie_index" in cue and (type(cue["movie_index"]) is not int or cue["movie_index"] < 0):
            raise HomeAssistantError("Stored movie indices must be nonnegative integers")
        if any(key in cue and type(cue[key]) is not bool for key in ("off", "stored_movie")):
            raise HomeAssistantError("Show off and stored_movie fields must be booleans")

    def _validate_scenes(self, plan):
        """Dry-run the complete programme without changing any live state."""
        shadow = {group: self._draft(group) for group in self.groups}
        for group, draft in shadow.items():
            draft["state"] = {key: (False, value[1], value[2]) for key, value in draft["state"].items()}
            draft["pane_states"].clear()
            draft["pane_overrides"].clear()
            draft["combined_selected"].clear()
        for target in plan["targets"]:
            for group, members, index, _ in self._bindings(self.entities[target]):
                if index is not None:
                    member = members[0]
                    panes = group.pane_maps.get(member["mac"], [])
                    if (not panes or any(not pane for pane in panes)
                            or any(type(pixel) is not int for pane in panes for pixel in pane)
                            or sorted(pixel for pane in panes for pixel in pane) != list(range(member["led_count"]))):
                        raise HomeAssistantError("Show pane maps must cover each controller exactly once: " + target)
        scenes, offset = {}, 0.0
        for section in plan["sections"]:
            for cue in section["cues"]:
                scenes.setdefault(round(offset + cue["at"], 6), []).append(cue)
            offset += section["duration"]
        native = set()
        for at in sorted(scenes):
            drafts, movies = self._compile_scene(scenes[at], 100, shadow)
            if native.intersection(set(drafts) - set(movies)):
                raise HomeAssistantError("Stored movies must finish the show; real-time scenes cannot follow them")
            shadow.update(drafts)
            native.update(movies)

    @staticmethod
    def _stage(group, draft, members, index, combined, on, data, now):
        if index is not None:
            mac = members[0]["mac"]
            panes = group.pane_maps.get(mac, [])
            if not 0 <= index < len(panes) or not panes[index]:
                raise HomeAssistantError("Capture a valid pane map before starting the show")
            if mac not in draft["pane_overrides"]:
                for pane in range(len(panes)):
                    key = (mac, pane)
                    draft["pane_states"][key] = draft["state"][mac]
                    inherited = draft["effects"][mac]
                    draft["pane_effects"][key] = inherited if inherited in group.pane_patterns.get(key, {}) else "Solid"
            draft["pane_overrides"].add(mac)
            key = (mac, index)
            old = draft["pane_states"][key]
            effect = data.get("effect", "Solid" if "rgb_color" in data else draft["pane_effects"].get(key, "Solid"))
            if effect not in ("Solid", *group.pane_patterns.get(key, {})):
                raise HomeAssistantError("Show effect requires a captured pane map")
            color = tuple(data.get("rgb_color", old[2]))
            if len(color) == 3:
                color += (0,)
            draft["pane_states"][key] = (on, data.get("brightness", old[1]), color)
            draft["pane_effects"][key] = effect
            return
        for member in members:
            key = member["mac"]
            old = draft["state"][key]
            effect = data.get("effect", "Solid" if "rgb_color" in data else draft["effects"][key])
            if effect in _CHASES:
                zones = group.house_zones if effect == "House and Garden Chase" else group.chase_zones
                if not zones or len(members) != len(group.members):
                    if "effect" in data:
                        raise HomeAssistantError("Show chase requires the complete mapped Front group")
                    effect = "Solid"
            if effect not in ("Solid", *group.patterns[key], *_CHASES):
                raise HomeAssistantError("Show effect requires a captured controller map")
            if on and effect in _CHASES and draft["effects"][key] != effect:
                draft["chase_started"] = now
            color = tuple(data.get("rgb_color", old[2]))
            if len(color) == 3:
                color += (0,)
            draft["state"][key] = (on, data.get("brightness", old[1]), color)
            draft["effects"][key] = effect
            draft["pane_overrides"].discard(key)
            if combined and key in group.combined_geometry:
                draft["combined_selected"].add(key)
            else:
                draft["combined_selected"].discard(key)

    def _compile_scene(self, cues, brightness_pct, previous=None):
        """Resolve and stage one scene; shared by preflight and playback."""
        drafts, movies, lit_groups = {}, {}, set()
        now = time.monotonic()
        for cue in cues:
            on = not cue.get("off") and brightness_pct > 0
            brightness = max(1, min(255, round(cue.get("brightness", 255) * brightness_pct / 100)))
            for target in cue["targets"]:
                entity = self.entities[target]
                bindings = self._bindings(entity)
                effect = cue.get("effect")
                if on and cue.get("stored_movie"):
                    group = bindings[0][0]
                    available = sorted(self._movies.get(group, {}))
                    effect = available[int(cue.get("movie_index", 0)) % len(available)] if available else None
                available_effects = entity.effect_list or []
                if on and effect is not None and effect not in available_effects:
                    effect = cue.get("fallback_effect") if cue.get("fallback_effect") in available_effects else None
                data = {"brightness": brightness} if on else {}
                if on and effect is None and (cue.get("stored_movie") or "effect" in cue):
                    if "fallback_rgb" not in cue:
                        raise HomeAssistantError("Show effect is unavailable: " + target + ": " + str(cue.get("effect", "stored movie")))
                    effect = "Solid"
                    data["rgb_color"] = cue["fallback_rgb"]
                elif "rgb" in cue and on:
                    data["rgb_color"] = cue["rgb"]
                if effect is not None and on:
                    data["effect"] = effect
                if on and effect and effect.startswith("Movie: "):
                    group = bindings[0][0]
                    if effect not in self._movies.get(group, {}):
                        raise HomeAssistantError("Stored movie was not prepared before the soundtrack")
                    selection = (effect, brightness)
                    if group in movies and movies[group] != selection:
                        raise HomeAssistantError("A show scene cannot request two different movies on the same group")
                    movies[group] = selection
                    continue
                for group, members, index, combined in bindings:
                    if on:
                        lit_groups.add(group)
                    if group not in drafts:
                        if previous is not None and group in previous:
                            drafts[group] = {key: value.copy() if isinstance(value, (dict, set)) else value for key, value in previous[group].items()}
                        else:
                            drafts[group] = self._draft(group)
                    self._stage(group, drafts[group], members, index, combined, on, data, now)
        if lit_groups.intersection(movies):
            raise HomeAssistantError("A show scene cannot mix real-time lights and a stored movie on the same group")
        return drafts, movies

    async def apply(self, cues, brightness_pct):
        if not self._prepared:
            raise HomeAssistantError("Show lights have not been prepared")
        async with self._locked():
            try:
                drafts, movies = self._compile_scene(cues, brightness_pct)
                if not (set(drafts) | set(movies)).issubset(self._owned):
                    raise HomeAssistantError("Show no longer owns its lighting group")
                for group, draft in drafts.items():
                    if group in movies:
                        continue
                    if not group.active or group.native_effect:
                        raise HomeAssistantError("Show stream stopped unexpectedly; restart the show to resynchronise")
                    for key, value in draft.items():
                        setattr(group, key, value)
                # Every same-time off/on and pane operation is now committed.
                # A group sends one complete frame, never intermediate scenes.
                await _complete([group.send() for group in drafts if group not in movies])
                await _complete([self._play_movie(group, *selection) for group, selection in movies.items()])
                for group in set(drafts) | set(movies):
                    group.notify()
            except BaseException:
                await self._stop_locked()
                raise

    async def _play_movie(self, group, effect, brightness):
        if group.native_effect == effect:
            # With only one shared movie, the second finale slot continues it.
            if group.native_brightness != brightness:
                await group.native_level(round(brightness * 100 / 255))
                group.native_brightness = brightness
            return
        was_active = group.active
        self._detach(group)
        self._black(group)
        group.hold_idle = False
        if was_active:
            # One immediate black frame, without the ordinary 1-second idle
            # drain: the prepared native mode immediately takes ownership next.
            await group.send()
        selection = self._movies[group][effect]
        await _complete([
            group.native_level(round(brightness * 100 / 255)),
            *[group.call(member, self._paths[group][member["mac"]], method="POST",
                         token=group.tokens[member["mac"]], payload={"id": selection[member["mac"]]})
              for member in group.members],
        ])
        await group.mode("movie")
        group.native_effect = effect
        group.native_brightness = brightness
        group.error = False

    async def _stop_group(self, group):
        group.hold_idle = False
        self._detach(group)
        self._black(group)
        group.native_effect = None
        try:
            await group.shutdown("off")
            group.error = False
        except Exception:
            group.error = True
            raise
        finally:
            group.notify()

    async def _stop_locked(self, raise_errors=False):
        groups = list(self._owned)
        self._retained.clear()
        self._prepared = False
        results = await asyncio.gather(*(self._stop_group(group) for group in groups), return_exceptions=True)
        errors = []
        for group, result in zip(groups, results):
            if isinstance(result, BaseException):
                _LOGGER.error("Could not switch off a Christmas show group: %s", result)
                errors.append(result)
            else:
                self._owned.discard(group)
        if errors and raise_errors:
            raise errors[0]

    async def finish(self, keep_movie=True):
        async with self._locked():
            keep = {group for group in self._owned if keep_movie and group.native_effect and not group.active}
            await _complete([self._stop_group(group) for group in self._owned - keep])
            self._owned = keep
            self._retained = {group: group.native_effect for group in keep}
            for group in keep:
                group.hold_idle = False
            self._prepared = False

    async def stop(self):
        async with self._locked():
            # A later manual lighting choice releases a completed finale. Do
            # not turn that replacement scene off when stopping the old show.
            for group, effect in self._retained.items():
                if group.active or group.native_effect != effect:
                    self._owned.discard(group)
            await self._stop_locked(raise_errors=True)
