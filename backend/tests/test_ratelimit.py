"""Deterministic unit tests for the standalone rate-limiter primitive.

No wall-clock time is used: a fake clock is injected so the suite never sleeps
and never depends on real elapsed time.
"""

import threading

import pytest

from app.ratelimit import RateLimitResult, RateLimiter


class FakeClock:
    """A monotonic clock under test control."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(max_events: int = 3, window_seconds: float = 60.0, **kwargs):
    clock = kwargs.pop("clock", None) or FakeClock()
    return RateLimiter(max_events, window_seconds, clock=clock, **kwargs), clock


# 1. Below the limit is allowed.
def test_request_below_limit_is_allowed() -> None:
    limiter, _ = make_limiter(max_events=3)
    result = limiter.check("login:alice")
    assert result.allowed is True
    assert result.remaining == 2


# 2. Requests up to the configured limit are allowed.
def test_requests_up_to_limit_are_allowed() -> None:
    limiter, _ = make_limiter(max_events=3)
    results = [limiter.check("k") for _ in range(3)]
    assert all(r.allowed for r in results)
    assert [r.remaining for r in results] == [2, 1, 0]


# 3. The next request is denied.
def test_request_over_limit_is_denied() -> None:
    limiter, _ = make_limiter(max_events=3)
    for _ in range(3):
        limiter.check("k")
    denied = limiter.check("k")
    assert denied.allowed is False
    assert denied.remaining == 0


# 4. Denial returns a positive retry delay.
def test_denial_returns_positive_retry_delay() -> None:
    limiter, clock = make_limiter(max_events=1, window_seconds=30.0)
    limiter.check("k")
    clock.advance(10.0)
    denied = limiter.check("k")
    assert denied.allowed is False
    assert denied.retry_after == 20


def test_retry_delay_rounds_up_to_whole_seconds() -> None:
    limiter, clock = make_limiter(max_events=1, window_seconds=30.0)
    limiter.check("k")
    clock.advance(29.5)
    denied = limiter.check("k")
    assert denied.allowed is False
    assert denied.retry_after == 1


# 5. The window resets after expiration.
def test_window_resets_after_expiration() -> None:
    limiter, clock = make_limiter(max_events=2, window_seconds=60.0)
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed
    assert limiter.check("k").allowed is False
    clock.advance(60.0)
    fresh = limiter.check("k")
    assert fresh.allowed is True
    assert fresh.remaining == 1


# 6. Exactly at the expiration boundary behaves consistently.
def test_exact_boundary_is_treated_as_expired() -> None:
    limiter, clock = make_limiter(max_events=1, window_seconds=60.0)
    assert limiter.check("k").allowed
    clock.advance(60.0)  # exactly at the boundary
    assert limiter.check("k").allowed is True
    clock.advance(59.999)
    assert limiter.check("k").allowed is False


# 7. Different keys maintain independent counters.
def test_keys_are_independent() -> None:
    limiter, _ = make_limiter(max_events=2)
    assert limiter.check("a").allowed
    assert limiter.check("a").allowed
    assert limiter.check("a").allowed is False
    allowed_b = limiter.check("b")
    assert allowed_b.allowed is True
    assert allowed_b.remaining == 1


# 8. Invalid limits and window durations are rejected.
@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_max_events_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        RateLimiter(bad, 60.0)


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_invalid_window_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        RateLimiter(10, bad)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), "abc", None])
def test_non_finite_or_non_numeric_window_rejected(bad) -> None:
    with pytest.raises(ValueError):
        RateLimiter(10, bad)


@pytest.mark.parametrize("bad", [0, -5])
def test_invalid_max_keys_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        RateLimiter(10, 60.0, max_keys=bad)


def test_empty_key_rejected() -> None:
    limiter = RateLimiter(10, 60.0)
    with pytest.raises(ValueError):
        limiter.check("")


# 9. Expired keys can be pruned and state does not grow indefinitely.
def test_prune_removes_expired_keys() -> None:
    limiter, clock = make_limiter(max_events=5, window_seconds=10.0)
    for i in range(5):
        limiter.check(f"k{i}")
    assert limiter.active_keys() == 5
    clock.advance(10.0)
    assert limiter.prune() == 5
    assert limiter.active_keys() == 0


def test_prune_keeps_live_keys() -> None:
    limiter, clock = make_limiter(max_events=5, window_seconds=10.0)
    limiter.check("old")
    clock.advance(9.0)
    limiter.check("new")
    clock.advance(1.5)  # "old" expired, "new" still live
    assert limiter.prune() == 1
    assert limiter.active_keys() == 1


def test_state_stays_bounded_by_max_keys() -> None:
    limiter, clock = make_limiter(max_events=1, window_seconds=100.0, max_keys=3)
    for i in range(3):
        assert limiter.check(f"k{i}").allowed
    # Capacity reached with live keys: a brand-new key is denied, not tracked.
    denied = limiter.check("overflow")
    assert denied.allowed is False
    assert denied.retry_after >= 1
    assert limiter.active_keys() == 3
    # After expiry the same key is accepted again and state stays bounded.
    clock.advance(100.0)
    assert limiter.check("overflow").allowed is True
    assert limiter.active_keys() == 1


def test_opportunistic_prune_keeps_dict_bounded_without_explicit_prune() -> None:
    limiter, clock = make_limiter(max_events=1, window_seconds=10.0, max_keys=100)
    for i in range(50):
        limiter.check(f"k{i}")
    assert limiter.active_keys() == 50
    clock.advance(10.0)
    limiter.check("later")  # triggers opportunistic prune of the 50 expired
    assert limiter.active_keys() == 1


# 10. Concurrent requests cannot exceed the configured allowance.
def test_concurrent_requests_do_not_exceed_allowance() -> None:
    limit = 100
    limiter = RateLimiter(limit, 60.0, clock=FakeClock())
    allowed_count = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal allowed_count
        for _ in range(50):
            if limiter.check("shared").allowed:
                with lock:
                    allowed_count += 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert allowed_count == limit


# 11. The injected clock makes tests deterministic without sleep().
def test_injected_clock_is_deterministic() -> None:
    clock = FakeClock(start=1000.0)
    limiter = RateLimiter(1, 5.0, clock=clock)
    assert limiter.check("k").allowed is True
    assert limiter.check("k").allowed is False
    clock.advance(5.0)
    assert limiter.check("k").allowed is True


def test_result_is_frozen_dataclass() -> None:
    result = RateLimitResult(allowed=True, remaining=1, retry_after=0)
    with pytest.raises(Exception):
        result.allowed = False  # type: ignore[misc]


# 12. peek is read-only: it reports blocked status without consuming budget.
def test_peek_does_not_consume_budget() -> None:
    limiter, _ = make_limiter(max_events=2)
    assert limiter.peek("k").allowed is True
    assert limiter.peek("k").remaining == 2  # unchanged by peeking
    limiter.check("k")
    assert limiter.peek("k").remaining == 1
    limiter.check("k")
    blocked = limiter.peek("k")
    assert blocked.allowed is False
    assert blocked.retry_after >= 1
    # Peeking repeatedly must not change the state.
    assert limiter.peek("k").allowed is False
    assert limiter.peek("k").allowed is False


def test_peek_unknown_key_is_allowed_and_does_not_create_it() -> None:
    limiter, _ = make_limiter(max_events=1)
    assert limiter.peek("never-seen").allowed is True
    assert limiter.active_keys() == 0


def test_peek_after_window_expiry_is_allowed() -> None:
    limiter, clock = make_limiter(max_events=1, window_seconds=10.0)
    limiter.check("k")
    assert limiter.peek("k").allowed is False
    clock.advance(10.0)
    assert limiter.peek("k").allowed is True
    assert limiter.peek("k").remaining == 1


def test_peek_rejects_empty_key() -> None:
    limiter = RateLimiter(10, 60.0)
    with pytest.raises(ValueError):
        limiter.peek("")
