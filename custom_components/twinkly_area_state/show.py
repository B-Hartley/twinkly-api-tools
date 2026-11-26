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

_LOGGER = logging.getLogger(__name__)
AUDIO_START_TIMEOUT = 10.0
MAX_CUE_LATENESS = 1.5
# Sonos positions are quantised to seconds. Ignore normal sampling noise,
# slew the local clock rather than jump, and reject only major discontinuities.
POSITION_DEADBAND = 0.45
MAX_POSITION_DRIFT = 8.0
CLOCK_SLEW_RATE = 0.5


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
        self.last_error = None
        self._drift_since = None
        self._clock_correction = 0.0
        self._clock_checked_at = None
        self._last_audio_progress = None
        self._audio_ended = False
        self._lifecycle_lock = asyncio.Lock()

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
        unknown = [key for key in plan["targets"] if key not in by_key]
        if unknown:
            raise HomeAssistantError("Show lights not registered: " + ", ".join(unknown))
        entities = {by_key[key].entity_id: by_key[key] for key in plan["targets"]}
        for section in plan["sections"]:
            for cue in section["cues"]:
                cue["targets"] = [by_key[key].entity_id for key in cue["targets"]]
        plan["targets"] = [by_key[key].entity_id for key in plan["targets"]]
        missing = [target for target in plan["targets"]
                   if not self.hass.states.get(target) or
                   self.hass.states.get(target).state in ("unavailable", "unknown")]
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
        return base.rstrip("/") + "/" + plan["soundtrack"]["file"]

    async def start(self, call):
        async with self._lifecycle_lock:
            await self._start(call)

    async def start_countdown(self, call):
        async with self._lifecycle_lock:
            await self._start(call, "countdown_test.json")

    async def _start(self, call, filename="christmas_show.json"):
        self.guard()
        if self.task and not self.task.done():
            raise HomeAssistantError("Christmas show already running")
        plan = await self.hass.async_add_executor_job(
            lambda: json.loads(Path(__file__).with_name(filename).read_text(encoding="utf-8")))
        build_timeline(plan)
        entities = self.resolve(plan)
        self.audio_ready(plan, call.data)
        self.guard()
        if self.task and not self.task.done():
            raise HomeAssistantError("Christmas show already running")
        # A previous completed show may still own its stored-movie finale.
        if self.lights is not None:
            await self.lights.stop()
        self.lights = ShowLights(self.hass, self.ctx, entities)
        self.last_error = None
        self.task = self.hass.async_create_background_task(
            self.run(plan, dict(call.data)), "Christmas soundtrack show")

    async def stop(self, call=None):
        async with self._lifecycle_lock:
            if self.task and not self.task.done():
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            # Retain the session after completion so this also stops the final movie.
            if self.lights is not None:
                await self.lights.stop()
                self.lights = None

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
            await self.lights.apply(cues, brightness)
        # Calibration moves light cues, never the end of the audio programme.
        # In particular, a negative offset must not cut off Santa's farewell.
        end = max(epoch + duration, audio[2] + duration) if audio else epoch + duration
        await self.wait_until(end, audio)

    async def cleanup_audio(self, speaker, url, volume, volume_changed, play_requested,
                            original_media=None):
        if not volume_changed:
            return
        state = self.hass.states.get(speaker)
        ours = bool(state and media_identity(state.attributes.get("media_content_id")) == media_identity(url))
        # Do not stop or change the volume of a replacement started by the user.
        unchanged = bool(state and state.state in ("idle", "standby", "off", "paused") and
                         media_identity(state.attributes.get("media_content_id")) == original_media)
        if not ours and not unchanged:
            return
        try:
            if ours:
                await self.call("media_player", "media_stop", speaker)
        except Exception:
            _LOGGER.exception("Show soundtrack could not be stopped")
        finally:
            if volume is not None:
                try:
                    await self.call("media_player", "volume_set", speaker, volume_level=volume)
                except Exception:
                    _LOGGER.exception("Show speaker volume could not be restored")

    async def run(self, plan, options):
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
        try:
            timeline, duration = build_timeline(plan)
            url = self.audio_ready(plan, options)
            # Authentication, map checks, stored movie lookups and the RT black
            # warmup must finish before the soundtrack clock starts.
            await self.lights.prepare(plan)
            self.guard()
            audio = None
            if url is not None:
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
                await self.call("media_player", "play_media", speaker,
                                media_content_id=url, media_content_type="music")
                audio_epoch = await self.wait_for_audio(
                    speaker, url, requested_at, requested_mono, duration)
                audio = (speaker, url, audio_epoch, duration)
                epoch = audio_epoch + options.get("offset_ms", 0) / 1000.0
            else:
                epoch = monotonic()
            await self.run_timeline(timeline, duration, epoch,
                                    options.get("brightness_pct", 2), audio)
            await self.lights.finish(keep_movie=True)
            completed = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            _LOGGER.exception("Christmas show failed: %s", exc)
        finally:
            # Stop the owned soundtrack promptly, even if a controller takes
            # time to acknowledge its off command during error/cancel cleanup.
            await self.cleanup_audio(speaker, url, volume, volume_changed,
                                     play_requested, original_media)
            if not completed:
                try:
                    await self.lights.stop()
                except Exception:
                    _LOGGER.exception("Christmas show lights could not be stopped")
