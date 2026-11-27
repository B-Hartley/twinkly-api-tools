"""Bounded asynchronous controller operations, independent of realtime UDP."""
import asyncio
import logging
from contextlib import asynccontextmanager

_LOGGER = logging.getLogger(__name__)

def communication_error(error):
    text = str(error)
    return any(part in text for part in (
        "TimeoutError", "ClientConnectionError", "ClientOSError",
        "ServerDisconnectedError", "ConnectionResetError", "ClientPayloadError",
        "HTTP 401", "HTTP 403", "HTTP 502", "HTTP 503", "HTTP 504",
        "Device code 1101", "Incomplete or malformed controller JSON",
        "Cannot connect", "Connection refused", "timed out",
    ))

class ControllerBatch:
    """Share a three-controller limit; sticky one-worker fallback until reload.

    Callbacks own a complete controller transaction. Never replay writes.
    Already-started operations finish before the next wave. Read callers may
    report partial results using the failed-result predicate.
    """
    def __init__(self, limit=3):
        if not 1 <= limit <= 6:
            raise ValueError("Invalid controller concurrency")
        self.limit = limit
        self._active = 0
        self._condition = asyncio.Condition()
        self._locks = {}

    def fallback(self):
        if self.limit != 1:
            self.limit = 1
            _LOGGER.warning("Twinkly controller communications falling back to one worker until integration reload")

    @asynccontextmanager
    async def slot(self, member):
        key = member.get("ip", member.get("mac"))
        if key is None:
            raise ValueError("Controller identity required")
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            async with self._condition:
                await self._condition.wait_for(lambda: self._active < self.limit)
                self._active += 1
            try:
                yield
            finally:
                async with self._condition:
                    self._active -= 1
                    self._condition.notify_all()

    async def run(self, members, callback, *, stop_on_error=False,
                  return_exceptions=False, failed_result=None):
        async def one(member):
            async with self.slot(member):
                try:
                    result = await callback(member)
                    if failed_result is not None and failed_result(result):
                        self.fallback()
                    return result
                except Exception as exc:
                    if communication_error(exc):
                        self.fallback()
                    raise

        remaining = list(members)
        results = []
        while remaining:
            wave = remaining[:self.limit]
            del remaining[:len(wave)]
            values = await asyncio.gather(*(one(m) for m in wave), return_exceptions=True)
            results.extend(values)
            if any(isinstance(v, BaseException) for v in values):
                self.fallback()
                if stop_on_error:
                    break
        if not return_exceptions:
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        return results
