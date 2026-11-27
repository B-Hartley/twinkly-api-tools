"""Soundtrack-led Christmas choreography; starts only on an explicit service call."""
import asyncio
from asyncio import sleep
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qsl, unquote, urlsplit

from homeassistant.exceptions import HomeAssistantError

from .show_lights import ShowLights
from .house_lights import HouseLights

_LOGGER = logging.getLogger(__name__)
AUDIO_START_TIMEOUT = 10.0
MAX_CUE_LATENESS = 1.5
# Sonos positions are quantised to seconds. Ignore normal sampling noise,
# slew the local clock rather than jump, and reject only major discontinuities.
POSITION_DEADBAND = 0.45
MAX_POSITION_DRIFT = 8.0
CLOCK_SLEW_RATE = 0.5
PREPARED_TTL = 15 * 60
SHOW_VARIANTS = {
    "original": "christmas_show.json",
    "house_chase": "christmas_show_house_chase.json",
}
DEFAULT_OPTIONS = {"audio": True, "audio_base": "", "brightness_pct": 2,
                   "house_brightness_pct": 100, "volume": 0.05, "offset_ms": 0}


def twinkly_plan(plan):
    """Give the native scene engine only its own targets and cues."""
    return {**plan,
            "targets": [key for key in plan["targets"] if not key.startswith("house:")],
            "sections": [{**section, "cues": [
                {**cue, "targets": [key for key in cue["targets"] if not key.startswith("house:")]}
                for cue in section["cues"]
                if any(not key.startswith("house:") for key in cue["targets"])
            ]} for section in plan["sections"]]}


def media_identity(value):
    """Compare the requested URI, preserving its path and version query."""
    if not isinstance(value, str) or not value:
        return None
    uri = urlsplit(value.replace("&amp;", "&"))
    return (uri.scheme.casefold(), uri.netloc.casefold(), unquote(uri.path),
            tuple(sorted(parse_qsl(uri.query, keep_blank_values=True))))


def media_position(state, now=None):
    """Extrapolate the position from HA's last valid position timestamp."""
    if state is None:
        return None
    position = state.attributes.get("media_position")
    updated = state.attributes.get("media_position_updated_at")
    if isinstance(position, bool) or not isinstance(position, (int, float)):
        return None
    if not math.isfinite(position) or position < 0:
        return None
    if isinstance(updated, str):
        try:
            updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        return None
    now = now or datetime.now(timezone.utc)
    age = (now - updated).total_seconds()
    if age < -1:
        return None
    return position + max(0.0, age), updated


def build_timeline(plan):
    """Flatten chapter-relative cues onto the one soundtrack's absolute clock."""
    soundtrack = plan.get("soundtrack") or {}
    filename = soundtrack.get("file")
    if (not isinstance(filename, str) or not filename or
            Path(filename).name != filename or "\\" in filename or
            filename in (".", "..")):
        raise HomeAssistantError("The show requires a single soundtrack filename")
    duration = soundtrack.get("duration")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
            not math.isfinite(duration) or duration <= 0):
        raise HomeAssistantError("The soundtrack duration is invalid")
    declared = set(plan.get("targets", []))
    if not declared:
        raise HomeAssistantError("The show has no light targets")
    groups = {}
    start = 0.0
    for section in plan.get("sections", []):
        length = section.get("duration")
        if (isinstance(length, bool) or not isinstance(length, (int, float)) or
                not math.isfinite(length) or length <= 0):
            raise HomeAssistantError("The show has an invalid chapter duration")
        for cue in section.get("cues", []):
            at = cue.get("at")
            if (isinstance(at, bool) or not isinstance(at, (int, float)) or
                    not math.isfinite(at) or not 0 <= at < length):
                raise HomeAssistantError("A light cue falls outside its chapter")
            if not cue.get("targets") or not set(cue["targets"]) <= declared:
                raise HomeAssistantError("A light cue has undeclared targets")
            absolute = round(start + at, 6)
            groups.setdefault(absolute, []).append(cue)
        start = round(start + length, 6)
    if abs(start - duration) > 0.02:
        raise HomeAssistantError("The light timeline and soundtrack durations differ")
    return sorted(groups.items()), float(duration)


class Show:
    def __init__(self, hass, ctx):
        self.hass = hass
        self.ctx = ctx
        self.task = None
        self.lights = None
        self.house_lights = None
        self.prepared = None
        self._ready_task = None
        self._pending_audio_cleanup = None
        self.status = "idle"
        self.kind = None
        self.variant = "original"
        self._listeners = set()
        self._house_brightness = 100
        self.last_error = None
        self._drift_since = None
        self._clock_correction = 0.0
        self._clock_checked_at = None
        self._last_audio_progress = None
        self._audio_ended = False
        self._lifecycle_lock = asyncio.Lock()

    def add_listener(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def set_status(self, status):
        self.status = status
        for callback in tuple(self._listeners):
            callback()

    def cleanup_error(self, message):
        self.last_error = (self.last_error + "; " + message) if self.last_error else message
        self.set_status("error")

    def guard(self):
        if self.ctx.get("mapping"):
            self.ctx["mapping"].guard()

    async def call(self, domain, service, entity, **data):
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity, **data}, blocking=True)

    def resolve(self, plan):
        """Retain installations' actual registry IDs and combined bush entities."""
        by_key = {}
        for group in self.ctx.get("virtual_groups", []):
            for entity in group.entities:
                key = getattr(entity, "show_key", None)
                if key and entity.entity_id:
                    if key in by_key and by_key[key].entity_id != entity.entity_id:
                        raise HomeAssistantError("Ambiguous show light: " + key)
                    by_key[key] = entity
        unknown = [key for key in plan["targets"] if not key.startswith("house:") and key not in by_key]
        if unknown:
            raise HomeAssistantError("Show lights not registered: " + ", ".join(unknown))
        entities = {by_key[key].entity_id: by_key[key] for key in plan["targets"] if not key.startswith("house:")}
        for section in plan["sections"]:
            for cue in section["cues"]:
                cue["targets"] = [key if key.startswith("house:") else by_key[key].entity_id for key in cue["targets"]]
        plan["targets"] = [key if key.startswith("house:") else by_key[key].entity_id for key in plan["targets"]]
        missing = [target for target in plan["targets"]
                   if not target.startswith("house:") and
                   (not self.hass.states.get(target) or
                    self.hass.states.get(target).state in ("unavailable", "unknown"))]
        if missing:
            raise HomeAssistantError("Show lights unavailable: " + ", ".join(missing))
        return entities

    def audio_ready(self, plan, options):
        """Audio is requested explicitly; a failed soundtrack is not a silent show."""
        if not options.get("audio", True):
            return None
        base = options.get("audio_base", "").strip()
        if not base:
            raise HomeAssistantError(
                "Set audio_base to the folder containing " + plan["soundtrack"]["file"] + ", or use audio: false for a silent rehearsal")
        speaker = plan["speaker"]
        state = self.hass.states.get(speaker)
        if state is None or state.state not in ("idle", "standby", "off", "paused"):
            raise HomeAssistantError("The show speaker must be idle or paused before starting")
        if len(state.attributes.get("group_members") or [speaker]) > 1:
            raise HomeAssistantError("Ungroup the show speaker before starting")
        if state.attributes.get("is_volume_muted"):
            raise HomeAssistantError("Unmute the show speaker before preparing an audible show")
        return base.rstrip("/") + "/" + plan["soundtrack"]["file"]

    def _require_audio_owner(self, speaker, url):
        state = self.hass.states.get(speaker)
        if (state is None or media_identity(state.attributes.get("media_content_id")) != media_identity(url)):
            raise HomeAssistantError("The prepared soundtrack was replaced; prepare again")
        if len(state.attributes.get("group_members") or [speaker]) > 1:
            raise HomeAssistantError("The prepared speaker was grouped; ungroup it and prepare again")
        return state

    async def start(self, call):
        async with self._lifecycle_lock:
            if self.prepared is not None:
                await self._start_prepared(call, "show")
            else:
                await self._start(call)

    async def start_countdown(self, call):
        async with self._lifecycle_lock:
            if self.prepared is not None:
                await self._start_prepared(call, "countdown")
            else:
                await self._start(call, "countdown_test.json")

    async def prepare(self, call):
        async with self._lifecycle_lock:
            await self._start(call, prepare_only=True)

    async def prepare_countdown(self, call):
        async with self._lifecycle_lock:
            await self._start(call, "countdown_test.json", prepare_only=True)

    async def start_prepared(self, call):
        """A button trigger only releases a ready session; never cold-starts."""
        async with self._lifecycle_lock:
            if self.prepared is None:
                raise HomeAssistantError("No programme is ready; prepare the show or countdown first")
            await self._start_prepared(call, self.prepared["kind"])

    async def _start(self, call, filename=None, prepare_only=False):
        self.guard()
        if self.task and not self.task.done():
            raise HomeAssistantError("Christmas show is already running or preparing; wait for Ready or stop it")
        kind = "countdown" if filename == "countdown_test.json" else "show"
        variant = call.data.get("variant", "original") if kind == "show" else "original"
        if variant not in SHOW_VARIANTS:
            raise HomeAssistantError("Unknown Christmas show variant: " + str(variant))
        filename = filename or SHOW_VARIANTS[variant]
        plan = await self.hass.async_add_executor_job(
            lambda: json.loads(Path(__file__).with_name(filename).read_text(encoding="utf-8")))
        build_timeline(plan)
        entities = self.resolve(plan)
        options = {**DEFAULT_OPTIONS, **dict(call.data)}
        self.audio_ready(plan, options)
        house = None
        if any(key.startswith("house:") for key in plan["targets"]):
            house = HouseLights(self.hass, plan.get("house_rooms"))
            house.resolve(plan)
        self.guard()
        if self.task and not self.task.done():
            raise HomeAssistantError("Christmas show already running")
        await self._release_prepared()
        if self._pending_audio_cleanup is not None:
            if not await self.cleanup_audio(**self._pending_audio_cleanup):
                raise HomeAssistantError("The previous soundtrack could not be released; stop it before preparing again")
            self._pending_audio_cleanup = None
        # A previous completed show may still own its stored-movie finale.
        await self._stop_lights()
        self.lights = ShowLights(self.hass, self.ctx, entities)
        self.house_lights = house
        self._house_brightness = options["house_brightness_pct"]
        self.kind, self.variant = kind, variant
        self.last_error = None
        self.set_status("preparing")
        self.task = self.hass.async_create_background_task(
            self._prepare_run(plan, options) if prepare_only else self.run(plan, options),
            "Prepare Christmas soundtrack" if prepare_only else "Christmas soundtrack show")

    async def _cancel_ready_watch(self):
        task, self._ready_task = self._ready_task, None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _stop_lights(self):
        operations = []
        if self.lights is not None:
            operations.append(self.lights.stop())
        if self.house_lights is not None:
            operations.append(self.house_lights.stop())
        results = await asyncio.gather(*operations, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _release_prepared(self):
        await self._cancel_ready_watch()
        prepared, self.prepared = self.prepared, None
        if prepared:
            await self.cleanup_audio(**prepared["audio_cleanup"])

    async def stop(self, call=None):
        async with self._lifecycle_lock:
            self.set_status("stopping")
            try:
                if self.task and not self.task.done():
                    self.task.cancel()
                    await asyncio.gather(self.task, return_exceptions=True)
                await self._release_prepared()
                if self._pending_audio_cleanup is not None:
                    if await self.cleanup_audio(**self._pending_audio_cleanup):
                        self._pending_audio_cleanup = None
                # Retain the session after completion so this also stops the final movie.
                await self._stop_lights()
                self.lights = self.house_lights = None
                self.set_status("error" if self._pending_audio_cleanup else "idle")
            except BaseException as exc:
                self.cleanup_error("Show cleanup did not finish: " + str(exc))
                raise

    def _check_paused(self, prepared):
        """Read raw paused position: extrapolating a paused timestamp is wrong."""
        url = prepared["url"]
        if url is None:
            return
        state = self.hass.states.get(prepared["plan"]["speaker"])
        if (state is None or state.state != "paused" or
                media_identity(state.attributes.get("media_content_id")) != media_identity(url)):
            raise HomeAssistantError("Prepared soundtrack changed or is no longer paused; prepare again")
        if len(state.attributes.get("group_members") or [prepared["plan"]["speaker"]]) > 1:
            raise HomeAssistantError("The prepared speaker was grouped; ungroup it and prepare again")
        if state.attributes.get("is_volume_muted"):
            raise HomeAssistantError("The prepared speaker was muted; unmute it and prepare again")
        position = state.attributes.get("media_position")
        if (isinstance(position, bool) or not isinstance(position, (int, float)) or
                not math.isfinite(position) or not 0 <= position <= 0.25):
            raise HomeAssistantError("Prepared soundtrack is not at the beginning; prepare again")

    async def _wait_for_paused_zero(self, prepared):
        deadline = monotonic() + AUDIO_START_TIMEOUT
        while monotonic() < deadline:
            try:
                self._check_paused(prepared)
                return
            except HomeAssistantError:
                await sleep(0.1)
        raise HomeAssistantError("Sonos did not confirm the soundtrack paused at zero; prepare again")

    async def _prepare_run(self, plan, options):
        cleanup = {"speaker": plan["speaker"], "url": None, "volume": None,
                   "volume_changed": False, "play_requested": False, "original_media": None}
        ready = False
        try:
            _, duration = build_timeline(plan)
            url = self.audio_ready(plan, options)
            cleanup["url"] = url
            # Reserve token lifetime for the ready hold as well as the show.
            native = twinkly_plan(plan)
            native["prepare_hold_seconds"] = PREPARED_TTL
            await self.lights.prepare(native)
            if self.house_lights is not None:
                await self.house_lights.prepare(plan, options["house_brightness_pct"], dark=False)
            self.guard()
            prepared = {"plan": plan, "options": options, "url": url,
                        "kind": self.kind, "variant": self.variant, "duration": duration,
                        "audio_cleanup": cleanup}
            if url is not None:
                self.audio_ready(plan, options)
                state = self.hass.states.get(plan["speaker"])
                cleanup["volume"] = state.attributes.get("volume_level")
                cleanup["original_media"] = media_identity(state.attributes.get("media_content_id"))
                cleanup["volume_changed"] = True
                # The transport must actually load/play the URI to buffer it.
                # It is inaudible until paused and rewound, including failure paths.
                await self.call("media_player", "volume_set", plan["speaker"], volume_level=0)
                requested_at, requested_mono = datetime.now(timezone.utc), monotonic()
                cleanup["play_requested"] = True
                await self.call("media_player", "play_media", plan["speaker"],
                                media_content_id=url, media_content_type="music")
                await self.wait_for_audio(plan["speaker"], url, requested_at, requested_mono, duration)
                self._require_audio_owner(plan["speaker"], url)
                await self.call("media_player", "media_pause", plan["speaker"])
                self._require_audio_owner(plan["speaker"], url)
                await self.call("media_player", "media_seek", plan["speaker"], seek_position=0)
                await self._wait_for_paused_zero(prepared)
                self._require_audio_owner(plan["speaker"], url)
                await self.call("media_player", "volume_set", plan["speaker"],
                                volume_level=options["volume"])
            self.lights.check_ready(duration)
            prepared["ready_at"] = monotonic()
            self.prepared = prepared
            self.set_status("ready")
            self._ready_task = self.hass.async_create_background_task(
                self._watch_prepared(prepared), "Christmas ready timeout")
            ready = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.set_status("error")
            _LOGGER.exception("Christmas preparation failed: %s", exc)
        finally:
            if not ready:
                self.prepared = None
                await self.cleanup_audio(**cleanup)
                try:
                    await self._stop_lights()
                except Exception as exc:
                    self.cleanup_error("Prepared lights could not be released: " + str(exc))
                    _LOGGER.exception("Prepared Christmas lights cleanup failed")

    async def _watch_prepared(self, prepared):
        while self.prepared is prepared:
            await sleep(1)
            error = None
            try:
                self.guard()
                self._check_paused(prepared)
                self.lights.check_ready(prepared["duration"])
                if monotonic() - prepared["ready_at"] >= PREPARED_TTL:
                    error = "Prepared show expired after 15 minutes; prepare again"
            except HomeAssistantError as exc:
                error = str(exc)
            if error:
                async with self._lifecycle_lock:
                    if self.prepared is not prepared:
                        return
                    self.last_error = error
                    self.set_status("expired" if "expired" in error else "error")
                    await self._release_prepared()
                    try:
                        await self._stop_lights()
                    except Exception as exc:
                        self.cleanup_error("Prepared lights could not be released: " + str(exc))
                        _LOGGER.exception("Expired Christmas lights cleanup failed")
                return

    async def _start_prepared(self, call, kind):
        prepared = self.prepared
        if prepared["kind"] != kind:
            raise HomeAssistantError("A different programme is prepared; use its Start action or prepare again")
        if call.data.get("variant", prepared["variant"]) != prepared["variant"]:
            raise HomeAssistantError("A different show variant is prepared; prepare the requested variant first")
        # Inherit prepared settings. Changing them requires an explicit re-prepare,
        # so Start never performs a hidden reload or expensive controller work.
        for key in DEFAULT_OPTIONS:
            if key in call.data and call.data[key] != prepared["options"][key]:
                raise HomeAssistantError("Prepared settings changed (" + key + "); prepare again")
        self.guard()
        self._check_paused(prepared)
        if monotonic() - prepared["ready_at"] >= PREPARED_TTL:
            raise HomeAssistantError("Prepared show expired; prepare again")
        self.lights.check_ready(prepared["duration"])
        await self._cancel_ready_watch()
        self.prepared = None
        # Stop can arrive before the new task has executed its first line.
        self._pending_audio_cleanup = prepared["audio_cleanup"]
        self.set_status("starting")
        self.task = self.hass.async_create_background_task(
            self.run(prepared["plan"], prepared["options"], prepared=prepared),
            "Prepared Christmas soundtrack show")

    async def wait_for_audio(self, speaker, url, requested_at, requested_mono, duration):
        deadline = monotonic() + AUDIO_START_TIMEOUT
        identity = media_identity(url)
        while monotonic() < deadline:
            state = self.hass.states.get(speaker)
            if (state and state.state == "playing" and
                    media_identity(state.attributes.get("media_content_id")) == identity):
                sample = media_position(state)
                if sample is not None:
                    position, updated = sample
                    # A cached position from an earlier run of the same file is unsafe.
                    if (updated >= requested_at and
                            position <= monotonic() - requested_mono + 2.0):
                        reported_duration = state.attributes.get("media_duration")
                        if (isinstance(reported_duration, (int, float)) and
                                reported_duration > 0 and
                                abs(reported_duration - duration) > 2.0):
                            raise HomeAssistantError(
                                "Sonos reports the wrong soundtrack duration; replace the audio pack")
                        return monotonic() - position
            await sleep(0.1)
        raise HomeAssistantError(
            "Sonos did not confirm the new soundtrack and a fresh playback position; check the audio URL")

    def check_audio(self, audio):
        speaker, url, epoch, duration = audio
        state = self.hass.states.get(speaker)
        now = monotonic()
        elapsed = now - epoch - self._clock_correction
        # Sonos reports whole seconds; allow its end event within the final second.
        if state and state.state in ("idle", "off") and elapsed >= duration - 1.0:
            return
        if state is None or media_identity(state.attributes.get("media_content_id")) != media_identity(url):
            raise HomeAssistantError("The soundtrack was replaced or became unavailable")
        if state.state == "paused" and elapsed >= duration - 1.0:
            # Sonos maps both PAUSED_PLAYBACK and STOPPED to HA's "paused"
            # while the track title remains. Accept natural completion only
            # for this soundtrack, with matching duration and end-position
            # evidence. A real pause earlier in the programme still aborts.
            reported = state.attributes.get("media_duration")
            position = state.attributes.get("media_position")
            matching_duration = (not isinstance(reported, bool) and
                                 isinstance(reported, (int, float)) and
                                 math.isfinite(reported) and abs(reported - duration) <= 2.0)
            at_end = (not isinstance(position, bool) and
                      isinstance(position, (int, float)) and math.isfinite(position) and
                      duration - 1.0 <= position <= duration + 1.0)
            # Some Sonos firmware resets position to zero on STOPPED. A recent
            # confirmed playing position near the end also establishes this
            # transition; never extrapolate an old paused position forward.
            recent_end = bool(self._last_audio_progress and
                              monotonic() - self._last_audio_progress[0] <= 1.0 and
                              self._last_audio_progress[1] >= duration - 1.5)
            if matching_duration and (at_end or recent_end or self._audio_ended):
                self._audio_ended = True
                return
        if state.state != "playing":
            raise HomeAssistantError("The soundtrack stopped or paused; the light show has stopped")
        sample = media_position(state)
        if sample is None:
            raise HomeAssistantError("The soundtrack playback position became unavailable")
        error = sample[0] - elapsed
        dt = 0.0 if self._clock_checked_at is None else max(0.0, now - self._clock_checked_at)
        self._clock_checked_at = now
        if abs(error) > MAX_POSITION_DRIFT:
            if self._drift_since is None:
                self._drift_since = now
            elif now - self._drift_since >= 1.0:
                raise HomeAssistantError("The soundtrack position jumped; restart the show to resynchronise")
            return
        self._drift_since = None
        # Positive error means audio is ahead: bring future light deadlines
        # forward. Negative error delays them. Bound the rate so the show
        # clock always runs forward and never replays an already-fired cue.
        residual = max(0.0, abs(error) - POSITION_DEADBAND)
        adjustment = min(residual, CLOCK_SLEW_RATE * min(dt, 0.5))
        if adjustment:
            self._clock_correction -= math.copysign(adjustment, error)
        self._last_audio_progress = (now, sample[0])

    async def wait_until(self, deadline, audio=None):
        while True:
            self.guard()
            if audio is not None:
                self.check_audio(audio)
            remaining = deadline + (self._clock_correction if audio else 0.0) - monotonic()
            if remaining <= 0:
                return
            await sleep(min(0.25, remaining))

    async def run_timeline(self, timeline, duration, epoch, brightness, audio=None):
        for at, cues in timeline:
            deadline = epoch + at
            await self.wait_until(deadline, audio)
            lateness = monotonic() - deadline - (self._clock_correction if audio else 0.0)
            # Never rush through a backlog of obsolete scenes after a timeout.
            # The prepared all-black opening is safe to repeat after audio startup.
            if lateness > MAX_CUE_LATENESS and not (at == 0 and all(c.get("off") for c in cues)):
                raise HomeAssistantError(
                    f"Light cue at {at:.2f}s was {lateness:.2f}s late; the show stopped to preserve synchronisation")
            native_cues = [{**cue, "targets": [key for key in cue["targets"] if not key.startswith("house:")]}
                          for cue in cues if any(not key.startswith("house:") for key in cue["targets"])]
            house_cues = [{**cue, "targets": [key for key in cue["targets"] if key.startswith("house:")]}
                         for cue in cues if any(key.startswith("house:") for key in cue["targets"])]
            operations = []
            if native_cues:
                operations.append(self.lights.apply(native_cues, brightness))
            if house_cues and self.house_lights is not None:
                operations.append(self.house_lights.apply(house_cues, self._house_brightness))
            results = await asyncio.gather(*operations, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        # Calibration moves light cues, never the end of the audio programme.
        # In particular, a negative offset must not cut off Santa's farewell.
        end = max(epoch + duration, audio[2] + duration) if audio else epoch + duration
        await self.wait_until(end, audio)

    async def cleanup_audio(self, speaker, url, volume, volume_changed, play_requested,
                            original_media=None):
        if not volume_changed:
            return True
        state = self.hass.states.get(speaker)
        ours = bool(state and media_identity(state.attributes.get("media_content_id")) == media_identity(url))
        # Do not stop or change the volume of a replacement started by the user.
        unchanged = bool(state and state.state in ("idle", "standby", "off", "paused") and
                         media_identity(state.attributes.get("media_content_id")) == original_media)
        if not ours and not unchanged:
            return True
        pending = dict(speaker=speaker, url=url, volume=volume,
                       volume_changed=volume_changed, play_requested=play_requested,
                       original_media=original_media)
        try:
            if ours:
                await self.call("media_player", "media_stop", speaker)
        except Exception as exc:
            # In particular a failed silent preload must never become audible
            # because restoring the volume ran after an unsuccessful stop.
            self._pending_audio_cleanup = pending
            self.cleanup_error("Show soundtrack could not be stopped: " + str(exc))
            _LOGGER.exception("Show soundtrack could not be stopped")
            return False
        # A different song can arrive while the stop request is in flight.
        state = self.hass.states.get(speaker)
        identity = media_identity(state.attributes.get("media_content_id")) if state else None
        if not state or identity not in (media_identity(url), original_media):
            return True
        if volume is not None:
            try:
                await self.call("media_player", "volume_set", speaker, volume_level=volume)
            except Exception as exc:
                self._pending_audio_cleanup = pending
                self.cleanup_error("Show speaker volume could not be restored: " + str(exc))
                _LOGGER.exception("Show speaker volume could not be restored")
                return False
        return True

    async def run(self, plan, options, prepared=None):
        speaker = plan["speaker"]
        url = None
        volume = None
        volume_changed = False
        play_requested = False
        original_media = None
        completed = False
        self._drift_since = None
        self._clock_correction = 0.0
        self._clock_checked_at = None
        self._last_audio_progress = None
        self._audio_ended = False
        self._house_brightness = options.get("house_brightness_pct", 100)
        try:
            timeline, duration = build_timeline(plan)
            if prepared is not None:
                url = prepared["url"]
                saved = prepared["audio_cleanup"]
                volume, volume_changed = saved["volume"], saved["volume_changed"]
                play_requested, original_media = saved["play_requested"], saved["original_media"]
                self._check_paused(prepared)
                self.lights.check_ready(duration)
            else:
                url = self.audio_ready(plan, options)
                # Preserve the existing one-call start; Prepare moves this work
                # into a separate, explicitly dark and paused session.
                await self.lights.prepare(twinkly_plan(plan))
                if self.house_lights is not None:
                    await self.house_lights.prepare(plan, self._house_brightness)
            self.guard()
            audio = None
            if url is not None:
                if prepared is None:
                    # Preparation can take time; recheck before taking the speaker.
                    self.audio_ready(plan, options)
                    state = self.hass.states.get(speaker)
                    volume = state.attributes.get("volume_level")
                    original_media = media_identity(state.attributes.get("media_content_id"))
                    # A cancelled request may already have changed the speaker.
                    volume_changed = True
                    await self.call("media_player", "volume_set", speaker,
                                    volume_level=options.get("volume", 0.05))
                requested_at = datetime.now(timezone.utc)
                requested_mono = monotonic()
                play_requested = True
                if prepared is not None:
                    # Issue blackout and resume together. Bridge acknowledgement
                    # must not put another long delay in front of Sonos playback.
                    operations = ([self.house_lights.blackout()] if self.house_lights is not None else [])
                    operations.append(self.call("media_player", "media_play", speaker))
                    results = await asyncio.gather(*operations, return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                else:
                    await self.call("media_player", "play_media", speaker,
                                    media_content_id=url, media_content_type="music")
                audio_epoch = await self.wait_for_audio(
                    speaker, url, requested_at, requested_mono, duration)
                audio = (speaker, url, audio_epoch, duration)
                epoch = audio_epoch + options.get("offset_ms", 0) / 1000.0
            else:
                if prepared is not None and self.house_lights is not None:
                    await self.house_lights.blackout()
                epoch = monotonic()
            self.set_status("running")
            await self.run_timeline(timeline, duration, epoch,
                                    options.get("brightness_pct", 2), audio)
            await self.lights.finish(keep_movie=True)
            completed = True
            self.set_status("finished")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.set_status("error")
            _LOGGER.exception("Christmas show failed: %s", exc)
        finally:
            # Stop the owned soundtrack promptly, even if a controller takes
            # time to acknowledge its off command during error/cancel cleanup.
            if await self.cleanup_audio(speaker, url, volume, volume_changed,
                                        play_requested, original_media):
                self._pending_audio_cleanup = None
            if not completed:
                try:
                    await self.lights.stop()
                except Exception as exc:
                    self.cleanup_error("Show lights could not be stopped: " + str(exc))
                    _LOGGER.exception("Christmas show lights could not be stopped")
            if self.house_lights is not None:
                try:
                    await self.house_lights.stop()
                except Exception as exc:
                    self.cleanup_error("House lights could not be restored: " + str(exc))
                    _LOGGER.exception("Christmas show house lights could not be restored")
