"""Route-level rate-limit configuration and shared limiters (Step 6.3C).

This module is the INTEGRATION layer on top of the dependency-free primitive in
``app.ratelimit``. It owns:

- validated environment configuration (malformed/zero/negative values raise,
  so protection is never silently disabled),
- application-scoped limiter instances (created once at import, shared by every
  request — never rebuilt per request),
- safe caller-key derivation (server-resolved identity and the direct ASGI
  client address; never request-body fields, never X-Forwarded-For),
- a FastAPI dependency factory that raises HTTP 429 with a ``Retry-After``
  header when a key is over its limit.

Configuration (all optional; defaults are intentionally generous for a
single-user local loopback demo):

- ``PS_RL_LOGIN_MAX`` / ``PS_RL_LOGIN_WINDOW`` — overall login attempts
  (default 30 per 300s).
- ``PS_RL_LOGIN_FAIL_MAX`` / ``PS_RL_LOGIN_FAIL_WINDOW`` — failed logins per
  username+client (default 5 per 900s).
- ``PS_RL_ASSISTANT_MAX`` / ``PS_RL_ASSISTANT_WINDOW`` — /assistant/ask and
  /assistant/propose-action per authenticated user (default 10 per 60s).
- ``PS_RL_DOCUMENT_MAX`` / ``PS_RL_DOCUMENT_WINDOW`` — upload and re-extract
  per authenticated user (default 20 per 3600s).
- ``PS_RL_MAX_KEYS`` — hard cap on tracked keys for every limiter (default 10000).
- ``PS_LLM_MAX_CONCURRENCY`` — simultaneous LLM generation calls (default 1).

Keys never contain raw usernames: the failed-login key uses a stable SHA-256
digest of the NOCASE-normalised username so two spellings of the same account
collide, while the raw value is never stored or logged.
"""

from __future__ import annotations

import hashlib
import os

from fastapi import Depends, HTTPException, Request, status

from .ratelimit import RateLimiter
from .security import require_user

# --- validated environment helpers ------------------------------------------


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


DEFAULT_LOGIN_MAX = 30
DEFAULT_LOGIN_WINDOW = 300.0
DEFAULT_LOGIN_FAIL_MAX = 5
DEFAULT_LOGIN_FAIL_WINDOW = 900.0
DEFAULT_ASSISTANT_MAX = 10
DEFAULT_ASSISTANT_WINDOW = 60.0
DEFAULT_DOCUMENT_MAX = 20
DEFAULT_DOCUMENT_WINDOW = 3600.0
DEFAULT_MAX_KEYS = 10_000
DEFAULT_LLM_MAX_CONCURRENCY = 1


def _max_keys() -> int:
    return _int_env("PS_RL_MAX_KEYS", DEFAULT_MAX_KEYS)


# Shared, application-scoped limiters. Created once at import so counters
# persist across requests; tests reset them explicitly when needed.
login_limiter = RateLimiter(
    _int_env("PS_RL_LOGIN_MAX", DEFAULT_LOGIN_MAX),
    _float_env("PS_RL_LOGIN_WINDOW", DEFAULT_LOGIN_WINDOW),
    max_keys=_max_keys(),
)
failed_login_limiter = RateLimiter(
    _int_env("PS_RL_LOGIN_FAIL_MAX", DEFAULT_LOGIN_FAIL_MAX),
    _float_env("PS_RL_LOGIN_FAIL_WINDOW", DEFAULT_LOGIN_FAIL_WINDOW),
    max_keys=_max_keys(),
)
assistant_limiter = RateLimiter(
    _int_env("PS_RL_ASSISTANT_MAX", DEFAULT_ASSISTANT_MAX),
    _float_env("PS_RL_ASSISTANT_WINDOW", DEFAULT_ASSISTANT_WINDOW),
    max_keys=_max_keys(),
)
document_limiter = RateLimiter(
    _int_env("PS_RL_DOCUMENT_MAX", DEFAULT_DOCUMENT_MAX),
    _float_env("PS_RL_DOCUMENT_WINDOW", DEFAULT_DOCUMENT_WINDOW),
    max_keys=_max_keys(),
)


def reset_all_limiters() -> None:
    """Clear all shared limiter state (used by tests)."""
    for limiter in (
        login_limiter,
        failed_login_limiter,
        assistant_limiter,
        document_limiter,
    ):
        limiter.reset()


# --- safe key derivation ----------------------------------------------------


def client_address(request: Request) -> str:
    """Return the direct ASGI peer address, or ``"unknown"``.

    Deliberately does NOT read X-Forwarded-For or similar headers: any client
    can forge them, and this deployment has no trusted proxy.
    """
    client = request.client
    host = getattr(client, "host", None)
    return host or "unknown"


def normalize_username(username: str) -> str:
    """NOCASE-normalise a username consistently with account lookup."""
    return username.strip().casefold() if isinstance(username, str) else ""


def username_digest(username: str) -> str:
    """Opaque, stable digest of a normalised username (never the raw value).

    Two spellings the auth layer treats as the same account normalise to the
    same digest, so an attacker cannot sidestep the failed-login limit by
    changing case or surrounding whitespace. SHA-256 (not salted) is used so
    the digest is stable across processes; usernames are low-entropy but the
    digest is only a limiter key, never exposed.
    """
    normalized = normalize_username(username)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def login_key(request: Request) -> str:
    return f"login:{client_address(request)}"


def failed_login_key(request: Request, username: str) -> str:
    return f"login-fail:{username_digest(username)}:{client_address(request)}"


def user_key(prefix: str, user_id: int) -> str:
    return f"{prefix}:{user_id}"


# --- FastAPI integration ----------------------------------------------------


def _retry_headers(retry_after: int) -> dict[str, str]:
    return {"Retry-After": str(retry_after)}


def enforce(limiter: RateLimiter, key: str) -> None:
    """Raise HTTP 429 with Retry-After if ``key`` is over its limit."""
    result = limiter.check(key)
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers=_retry_headers(result.retry_after),
        )


def raise_if_blocked(limiter: RateLimiter, key: str) -> None:
    """Raise HTTP 429 (side-effect free) when ``key`` is already blocked.

    Used as a pre-check before expensive work such as password hashing: a
    blocked key is refused without spending CPU. Uses the same 429 body and a
    positive ``Retry-After`` as ``enforce``.
    """
    result = limiter.peek(key)
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers=_retry_headers(result.retry_after),
        )


def assistant_rate_limit(
    request: Request,
    context=Depends(require_user),
) -> None:
    """Dependency: per-authenticated-user limit for assistant routes."""
    enforce(assistant_limiter, user_key("assistant", context.user_id))


def document_rate_limit(
    request: Request,
    context=Depends(require_user),
) -> None:
    """Dependency: per-authenticated-user limit for document ingest routes."""
    enforce(document_limiter, user_key("document", context.user_id))
