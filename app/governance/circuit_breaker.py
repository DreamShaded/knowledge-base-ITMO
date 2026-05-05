"""
CircuitBreaker per remote provider (ADR-0013 §3).
States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing) → CLOSED
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from typing import Awaitable, Callable, TypeVar

from app.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T")


class CBState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""


class CircuitBreaker:
    """
    Per-provider circuit breaker.
    Opens after `failure_threshold` consecutive failures,
    half-opens after `recovery_timeout` seconds to probe recovery.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state = CBState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CBState:
        return self._state

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            self._maybe_try_reset()
            if self._state == CBState.OPEN:
                raise CircuitBreakerOpen(
                    f"Circuit {self._name!r} is OPEN — provider unavailable"
                )

        try:
            result = await fn()
        except Exception as exc:
            async with self._lock:
                self._on_failure()
            raise exc from exc

        async with self._lock:
            self._on_success()
        return result

    def _maybe_try_reset(self) -> None:
        if self._state == CBState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = CBState.HALF_OPEN
                self._successes = 0
                log.info("half_open", name=self._name)

    def _on_failure(self) -> None:
        self._failures += 1
        self._successes = 0
        if self._state == CBState.HALF_OPEN or self._failures >= self._failure_threshold:
            self._state = CBState.OPEN
            self._opened_at = time.monotonic()
            log.warning("opened", name=self._name, failures=self._failures)

    def _on_success(self) -> None:
        self._failures = 0
        if self._state == CBState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self._success_threshold:
                self._state = CBState.CLOSED
                log.info("closed", name=self._name)
