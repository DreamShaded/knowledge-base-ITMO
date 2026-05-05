"""
WebChannelHealth FSM (ADR-0013).
States: HEALTHY → DEGRADED (single failure) → OUTAGE (3 failures in 60s).
Recovery: successful healthcheck → DEGRADED → HEALTHY after 60s healthy stretch.
"""
from __future__ import annotations

import time
from enum import Enum

from app.logging import get_logger

log = get_logger(__name__)

_FAILURE_WINDOW_SECONDS = 60.0
_OUTAGE_THRESHOLD = 3
_RECOVERY_HEALTHY_SECONDS = 60.0


class HealthState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OUTAGE = "outage"


class WebChannelHealth:
    """Thread-safe FSM tracking availability of the web channel."""

    def __init__(self) -> None:
        self._state = HealthState.HEALTHY
        self._failure_window_start: float = 0.0
        self._failures_in_window: int = 0
        self._healthy_since: float = 0.0

    @property
    def state(self) -> HealthState:
        return self._state

    @property
    def is_available(self) -> bool:
        return self._state != HealthState.OUTAGE

    @property
    def weight(self) -> float:
        """Channel weight multiplier — 0.0 forces web channel off."""
        return 0.0 if self._state == HealthState.OUTAGE else 1.0

    @property
    def warning(self) -> str | None:
        if self._state == HealthState.OUTAGE:
            return "web channel unavailable, retrieval из локальных источников"
        if self._state == HealthState.DEGRADED:
            return "web channel degraded — some requests may fail"
        return None

    def record_failure(self) -> None:
        now = time.monotonic()
        # Reset window counter if outside the window
        if now - self._failure_window_start > _FAILURE_WINDOW_SECONDS:
            self._failure_window_start = now
            self._failures_in_window = 0

        self._failures_in_window += 1
        self._healthy_since = 0.0

        if self._state == HealthState.HEALTHY:
            self._state = HealthState.DEGRADED
            log.warning("web_channel_degraded", failures=self._failures_in_window)
        elif self._state == HealthState.DEGRADED and self._failures_in_window >= _OUTAGE_THRESHOLD:
            self._state = HealthState.OUTAGE
            log.error("web_channel_outage", failures=self._failures_in_window)

    def record_success(self) -> None:
        now = time.monotonic()
        if self._state == HealthState.OUTAGE:
            # Outage recovery starts at DEGRADED — single success isn't enough
            self._state = HealthState.DEGRADED
            self._healthy_since = now
            log.info("web_channel_recovering")
            return

        if self._state == HealthState.DEGRADED:
            if self._healthy_since == 0.0:
                self._healthy_since = now
            elif now - self._healthy_since >= _RECOVERY_HEALTHY_SECONDS:
                self._state = HealthState.HEALTHY
                self._failures_in_window = 0
                self._healthy_since = 0.0
                log.info("web_channel_healthy")
