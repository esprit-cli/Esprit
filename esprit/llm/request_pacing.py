import asyncio
import inspect
import random
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

from esprit.config import Config


def _read_int(name: str, default: int, *, minimum: int | None = None) -> int:
    value = Config.get(name)
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _read_float(name: str, default: float, *, minimum: float | None = None) -> float:
    value = Config.get(name)
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


class RequestPacer:
    """Global LLM pacing across all agents to reduce provider burst throttling."""

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._next_request_at = 0.0
        self._cooldown_until = 0.0
        self._inflight = 0
        self._capacity = self._max_inflight()

    def _max_inflight(self) -> int:
        return _read_int("esprit_llm_max_inflight", 2, minimum=1)

    def _min_start_interval_s(self) -> float:
        interval_ms = _read_int("esprit_llm_min_start_interval_ms", 700, minimum=0)
        return interval_ms / 1000.0

    def _start_jitter_s(self) -> float:
        jitter_ms = _read_int("esprit_llm_start_jitter_ms", 250, minimum=0)
        return jitter_ms / 1000.0

    def _default_rate_limit_cooldown_s(self) -> float:
        return _read_float("esprit_llm_rate_limit_cooldown_s", 20.0, minimum=0.0)

    def _max_rate_limit_cooldown_s(self) -> float:
        return _read_float("esprit_llm_rate_limit_cooldown_max_s", 180.0, minimum=1.0)

    def _wait_poll_interval_s(self) -> float:
        return _read_float("esprit_llm_wait_poll_interval_s", 1.0, minimum=0.1)

    async def _invoke_wait_callback(
        self,
        on_wait: Callable[[str, float], Awaitable[None] | None] | None,
        reason: str,
        waited_s: float,
    ) -> None:
        if on_wait is None:
            return
        maybe_result = on_wait(reason, max(0.0, waited_s))
        if inspect.isawaitable(maybe_result):
            await maybe_result

    def _refresh_capacity_locked(self) -> None:
        self._capacity = self._max_inflight()

    async def _acquire_inflight_slot(
        self,
        on_wait: Callable[[str, float], Awaitable[None] | None] | None,
    ) -> None:
        started = time.monotonic()
        poll_s = self._wait_poll_interval_s()

        while True:
            with self._state_lock:
                self._refresh_capacity_locked()
                if self._inflight < self._capacity:
                    self._inflight += 1
                    return

            waited_s = time.monotonic() - started
            await self._invoke_wait_callback(on_wait, "queue", waited_s)
            await asyncio.sleep(poll_s)

    def _release_inflight_slot(self) -> None:
        with self._state_lock:
            self._inflight = max(0, self._inflight - 1)

    async def _reserve_start_slot(
        self,
        on_wait: Callable[[str, float], Awaitable[None] | None] | None,
    ) -> None:
        interval_s = self._min_start_interval_s()
        jitter_s = self._start_jitter_s()
        with self._state_lock:
            now = time.monotonic()
            reserved_start = max(now, self._next_request_at, self._cooldown_until)
            if jitter_s > 0:
                reserved_start += random.uniform(0.0, jitter_s)
            self._next_request_at = reserved_start + interval_s
            sleep_s = reserved_start - now

        if sleep_s <= 0:
            return

        if on_wait is None:
            await asyncio.sleep(sleep_s)
            return

        waited_s = 0.0
        poll_s = self._wait_poll_interval_s()
        remaining = sleep_s
        while remaining > 0:
            chunk = min(poll_s, remaining)
            await asyncio.sleep(chunk)
            waited_s += chunk
            remaining -= chunk
            await self._invoke_wait_callback(on_wait, "cooldown", waited_s)

    def register_rate_limit(self, retry_after_s: float | None = None) -> None:
        """Apply a global cooldown after provider 429s to prevent herd retries."""
        now = time.monotonic()
        cooldown_s = (
            retry_after_s
            if retry_after_s is not None and retry_after_s > 0
            else self._default_rate_limit_cooldown_s()
        )
        cooldown_s = min(cooldown_s, self._max_rate_limit_cooldown_s())
        with self._state_lock:
            self._cooldown_until = max(self._cooldown_until, now + cooldown_s)

    @asynccontextmanager
    async def request_slot(
        self,
        on_wait: Callable[[str, float], Awaitable[None] | None] | None = None,
    ) -> AsyncIterator[None]:
        await self._acquire_inflight_slot(on_wait)
        try:
            await self._reserve_start_slot(on_wait)
            yield
        finally:
            self._release_inflight_slot()


_GLOBAL_REQUEST_PACER = RequestPacer()


def get_request_pacer() -> RequestPacer:
    return _GLOBAL_REQUEST_PACER
