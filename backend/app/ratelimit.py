"""In-process, dependency-free rate limiting (Step 6.3B).

This module is DATA + LOGIC ONLY: it does not register routes, does not import
FastAPI, and is not wired into any endpoint yet (that is Step 6.3C). It exists
as a small, explainable primitive with deterministic unit tests.

Algorithm — fixed window
------------------------
Each caller-supplied ``key`` (for example ``"login:alice:127.0.0.1"`` or
``"assistant:42"``) gets its own counter. A window starts at the timestamp of
the first event for that key and lasts ``window_seconds``. Events are counted
until the window expires; once ``max_events`` events have been recorded, further
events are denied until the window rolls over.

A fixed window is chosen over a token bucket deliberately: it is trivial to
reason about, needs no background refill task, and is easy to defend in an
interview. The classic trade-off is a burst at a window boundary (a caller can
send up to ``2 * max_events`` events spanning two adjacent windows). For a local
loopback demo whose goal is to stop runaway loops and brute force — not to
enforce a metered quota — that is an acceptable and documented simplification.

Concurrency
-----------
All state lives in a single ``dict`` guarded by a ``threading.Lock``. FastAPI
runs synchronous endpoints on a thread pool, so two requests may touch the same
key simultaneously; the lock makes the check-and-increment atomic so concurrent
callers can never collectively exceed the allowance. The lock is held only for
in-memory arithmetic — never across I/O.

Memory bounding
---------------
State is a plain dict keyed by string, so it would grow forever if new keys kept
appearing. ``RateLimiter.prune(now)`` removes entries whose window has already
expired; ``check`` also prunes opportunistically when the dict grows past
``max_keys`` so callers that never call ``prune`` explicitly still stay bounded.
A hard cap (``max_keys``) bounds the worst case: when the dict is at capacity
with live keys, brand-new keys are denied rather than silently evicting active
state. This keeps memory O(max_keys) under any input.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_MAX_EVENTS = 60
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MAX_KEYS = 10_000


def _positive_number(value: object, name: str) -> float:
    """Return ``value`` as a positive float or raise ValueError.

    Rejects zero, negative, non-numeric and non-finite values so invalid
    configuration fails loudly instead of silently disabling the limit.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a finite positive number")
    if number <= 0:
        raise ValueError(f"{name} must be a positive number")
    return number


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a rate-limit check.

    ``allowed`` is True when the event was recorded. When False, ``retry_after``
    is a positive whole number of seconds until the window is expected to reset,
    suitable for a ``Retry-After`` header.
    """

    allowed: bool
    remaining: int
    retry_after: int


class _Window:
    """State for one key: the start of its current window and event count."""

    __slots__ = ("started_at", "count")

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.count = 0


class RateLimiter:
    """A fixed-window rate limiter tracking independent limits per key.

    ``max_events`` and ``window_seconds`` are validated in the constructor;
    invalid values raise ValueError. ``clock`` defaults to ``time.monotonic``
    and can be injected in tests for deterministic behaviour without sleeping.
    """

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(max_events, int) or isinstance(max_events, bool):
            raise ValueError("max_events must be a positive integer")
        if max_events <= 0:
            raise ValueError("max_events must be a positive integer")
        self.max_events = max_events
        self.window_seconds = _positive_number(window_seconds, "window_seconds")
        if not isinstance(max_keys, int) or isinstance(max_keys, bool) or max_keys <= 0:
            raise ValueError("max_keys must be a positive integer")
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}

    def check(self, key: str) -> RateLimitResult:
        """Record one event for ``key`` and report whether it is allowed.

        Atomic under the internal lock. Denial returns a positive retry delay
        equal to the seconds left in the window, rounded up.
        """
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            window = self._windows.get(key)
            if window is not None and self._expired(window, now):
                window = None
                del self._windows[key]
            if window is None:
                if len(self._windows) >= self.max_keys:
                    # At capacity with live keys: deny new keys rather than
                    # evicting active state. Retry after one full window.
                    return RateLimitResult(
                        allowed=False,
                        remaining=0,
                        retry_after=self._ceil_window(),
                    )
                window = _Window(now)
                self._windows[key] = window
            if window.count >= self.max_events:
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=self._retry_after(window, now),
                )
            window.count += 1
            return RateLimitResult(
                allowed=True,
                remaining=self.max_events - window.count,
                retry_after=0,
            )

    def prune(self, now: float | None = None) -> int:
        """Remove expired windows and return how many were removed."""
        moment = self._clock() if now is None else now
        with self._lock:
            before = len(self._windows)
            self._prune_locked(moment)
            return before - len(self._windows)

    def peek(self, key: str) -> RateLimitResult:
        """Report whether ``key`` is currently blocked, WITHOUT recording an event.

        Read-only and side-effect free: it never increments a counter, never
        creates a key, and never evicts live state. Used for a cheap pre-check
        before expensive work (e.g. password hashing). The returned result has
        ``allowed=True`` (with ``remaining`` reflecting the current budget) when
        the key may proceed, or ``allowed=False`` with a positive ``retry_after``
        when it is already at/over its limit.

        Because a peek does not consume an allowance, callers using it as an
        optimisation gate must still call ``check`` when the real event happens,
        so the atomic check-and-increment in ``check`` stays the single source
        of truth and concurrent requests cannot bypass the limit.
        """
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        now = self._clock()
        with self._lock:
            window = self._windows.get(key)
            if window is None or self._expired(window, now):
                return RateLimitResult(
                    allowed=True, remaining=self.max_events, retry_after=0
                )
            if window.count >= self.max_events:
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=self._retry_after(window, now),
                )
            return RateLimitResult(
                allowed=True,
                remaining=self.max_events - window.count,
                retry_after=0,
            )

    def active_keys(self) -> int:
        """Number of keys currently tracked (for tests and observability)."""
        with self._lock:
            return len(self._windows)

    def reset(self) -> None:
        """Drop all tracked state (used by tests and future wiring)."""
        with self._lock:
            self._windows.clear()

    # --- internal helpers (call while holding the lock) ---

    def _expired(self, window: _Window, now: float) -> bool:
        # A window is expired once ``now`` reaches started_at + window_seconds.
        # The boundary is inclusive: at exactly the expiry instant the window
        # has rolled over, so behaviour at the boundary is deterministic.
        return now - window.started_at >= self.window_seconds

    def _retry_after(self, window: _Window, now: float) -> int:
        remaining = (window.started_at + self.window_seconds) - now
        if remaining <= 0:
            # Window is actually expired; at least one second to be safe.
            return 1
        return max(1, int(remaining + 0.999999))

    def _ceil_window(self) -> int:
        return max(1, int(self.window_seconds + 0.999999))

    def _prune_locked(self, now: float) -> None:
        expired = [
            key
            for key, window in self._windows.items()
            if self._expired(window, now)
        ]
        for key in expired:
            del self._windows[key]
