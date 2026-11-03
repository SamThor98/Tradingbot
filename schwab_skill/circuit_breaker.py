"""
Process-wide circuit breaker for Schwab (and Discord) network instability.

Trips only after consecutive DNS / connect / read timeouts — not HTTP 401s —
so a single scan timeout cannot latch the process. OAuth token exchange and
refresh are recovery paths: they must still run while the breaker is open, and
a live token response calls ``record_success()`` to close it.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any


def _env_int(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class CircuitBreaker:
    name: str
    unstable_for_seconds: int = 300
    failure_threshold: int = 3
    failure_collapse_seconds: float = 0.0
    consecutive_failures: int = 0
    _stable_until_epoch: float = 0.0
    _last_counted_failure_mono: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _init_unstable_for_seconds: int = field(init=False, repr=False, default=300)
    _init_failure_threshold: int = field(init=False, repr=False, default=3)
    _init_failure_collapse_seconds: float = field(init=False, repr=False, default=0.0)

    def __post_init__(self) -> None:
        self._init_unstable_for_seconds = int(self.unstable_for_seconds)
        self._init_failure_threshold = int(self.failure_threshold)
        self._init_failure_collapse_seconds = float(self.failure_collapse_seconds)

    @property
    def connection_stable(self) -> bool:
        return time.time() >= self._stable_until_epoch

    def mark_unstable(self) -> None:
        self._stable_until_epoch = time.time() + float(self.unstable_for_seconds)

    def record_success(self) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self._stable_until_epoch = 0.0
            self._last_counted_failure_mono = 0.0

    def record_failure(self) -> bool:
        """Count one unstable error. Returns True if the breaker is now open."""
        with self._lock:
            now = time.monotonic()
            collapse = float(self.failure_collapse_seconds)
            if collapse > 0.0 and self._last_counted_failure_mono > 0.0:
                if (now - self._last_counted_failure_mono) < collapse:
                    return time.time() < self._stable_until_epoch
            self.consecutive_failures += 1
            self._last_counted_failure_mono = now
            threshold = max(1, int(self.failure_threshold))
            if self.consecutive_failures >= threshold:
                self.mark_unstable()
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self._stable_until_epoch = 0.0
            self._last_counted_failure_mono = 0.0
            self.unstable_for_seconds = self._init_unstable_for_seconds
            self.failure_threshold = self._init_failure_threshold
            self.failure_collapse_seconds = self._init_failure_collapse_seconds


def _message_contains(exc: Any, needle: str) -> bool:
    try:
        return needle.lower() in str(exc).lower()
    except Exception:
        return False


def is_transient_network_error(exc: Any) -> bool:
    """True for DNS, connect, and read timeouts — not HTTP 4xx/5xx bodies."""
    if isinstance(exc, socket.gaierror):
        return True

    name = exc.__class__.__name__
    if name in (
        "ReadTimeout",
        "ReadTimeoutError",
        "ConnectTimeout",
        "ConnectTimeoutError",
        "ConnectionError",
        "ConnectionResetError",
        "NameResolutionError",
    ):
        return True

    if _message_contains(exc, "getaddrinfo failed"):
        return True
    if _message_contains(exc, "NameResolutionError"):
        return True
    if _message_contains(exc, "Read timed out") or _message_contains(exc, "ReadTimeout"):
        return True
    if _message_contains(exc, "ConnectTimeout") or _message_contains(exc, "Connection timed out"):
        return True
    return False


def is_getaddrinfo_or_readtimeout_error(exc: Any) -> bool:
    return is_transient_network_error(exc)


def maybe_trip_breaker(exc: Any, breaker: CircuitBreaker) -> bool:
    """
    Count a transient network error toward the consecutive-failure latch.

    Returns True if this call opened the breaker (or it is already open after
    a counted failure). HTTP errors are ignored.
    """
    if not is_transient_network_error(exc):
        return False
    return breaker.record_failure()


schwab_circuit = CircuitBreaker(
    name="schwab",
    unstable_for_seconds=_env_int("SCHWAB_CIRCUIT_COOLDOWN_SECONDS", 300),
    failure_threshold=max(1, _env_int("SCHWAB_CIRCUIT_FAILURE_THRESHOLD", 3)),
    failure_collapse_seconds=_env_float("SCHWAB_CIRCUIT_FAILURE_COLLAPSE_SECONDS", 2.0),
)
discord_circuit = CircuitBreaker(name="discord", unstable_for_seconds=300)
