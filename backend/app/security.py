"""Route security: session enforcement, CSRF and origin checks (Step 6.2C-2).

This module is the single place route protection is defined. Business routers
depend on `require_user`, which:

- resolves the caller from the verified server-side session cookie (never from
  request-body fields such as user_id/role),
- returns 401 when the session is missing, invalid, expired, revoked, or
  belongs to an inactive user (fail closed),
- on unsafe methods (POST/PUT/PATCH/DELETE) additionally enforces CSRF
  (session-bound synchroniser/double-submit token in the `X-CSRF-Token`
  header) and, when a browser Origin/Referer is present, an allowed-origin
  check. Failures return 403.

Raw tokens, password hashes and CSRF hashes are never logged.
"""

import logging
import os
import sqlite3

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from . import auth
from .db import get_db

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "ps_session"
CSRF_COOKIE_NAME = "ps_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

# Time-limited secret-free config: whether to mark cookies Secure is decided by
# an explicit env flag, NEVER by trusting X-Forwarded-Proto (which any client
# can forge). Default is non-Secure for documented local HTTP development.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Endpoints that must remain reachable without a session.
PUBLIC_PATHS = frozenset({"/", "/health", "/docs", "/redoc", "/openapi.json"})


def cookie_secure() -> bool:
    """Whether to set Secure on cookies. Explicit opt-in via PS_AUTH_COOKIE_SECURE."""
    return os.environ.get("PS_AUTH_COOKIE_SECURE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def session_cookie_max_age() -> int:
    return auth.session_ttl_seconds()


def allowed_origins() -> list[str]:
    """Exact allowed browser origins (no wildcard), from main's policy."""
    from .main import ALLOWED_ORIGINS

    return ALLOWED_ORIGINS


def _origin_is_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    return origin in allowed_origins()


def _check_origin(request: Request) -> None:
    """Reject unsafe browser requests with a present-but-unapproved Origin/Referer.

    Requests without an Origin (e.g. same-origin form posts may omit it, and
    non-browser clients) are allowed through to the CSRF check — CSRF provides
    the primary defense. When a browser supplies an Origin, it must match.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        if not _origin_is_allowed(origin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Request origin is not allowed.",
            )
        return
    referer = request.headers.get("referer")
    if referer:
        # Compare scheme+host+port of the referer against allowed origins.
        allowed_prefixes = tuple(f"{o}/" for o in allowed_origins())
        if not any(referer == o or referer.startswith(p) for o, p in
                   zip(allowed_origins(), allowed_prefixes)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Request origin is not allowed.",
            )


def require_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> auth.SessionContext:
    """Authenticate the request and enforce CSRF on unsafe methods.

    Returns the verified `SessionContext` or raises 401/403. Identity is derived
    only from the session cookie; request-body user_id/role fields are ignored.
    """
    context = auth.resolve_session(conn, session_token or "")
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    if request.method in UNSAFE_METHODS:
        _check_origin(request)
        if not auth.verify_csrf(conn, context.session_id, csrf_header or ""):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token missing or invalid.",
            )

    return context
