"""Authentication endpoints: login, logout and current user (Step 6.2C-2).

- `POST /auth/login`  — verify credentials, create an opaque session, and set
  the session cookie (HttpOnly) plus a readable CSRF cookie.
- `POST /auth/logout` — revoke the current session and clear cookies. Always a
  predictable 200, even if the session was already invalid.
- `GET  /auth/me`     — return safe public fields for the verified caller.
- `GET  /auth/csrf`   — issue a pre-auth CSRF double-submit cookie so the
  frontend can protect the login request itself.

Raw session tokens are only ever placed in cookies, never in JSON bodies.
"""

import logging

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
import sqlite3

from .. import auth
from ..db import get_db
from ..ratelimit_config import (
    enforce,
    failed_login_key,
    failed_login_limiter,
    login_key,
    login_limiter,
    raise_if_blocked,
)
from ..schemas import CurrentUser, LoginRequest
from ..security import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    _check_origin,
    cookie_secure,
    require_user,
    session_cookie_max_age,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Invalid username or password."


def _set_session_cookies(response: Response, issued: auth.IssuedSession) -> None:
    secure = cookie_secure()
    max_age = session_cookie_max_age()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=issued.token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    # CSRF cookie is intentionally readable by JS (double-submit). Not HttpOnly,
    # but scoped to a single token and bound to the session server-side.
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=issued.csrf_token,
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    secure = cookie_secure()
    for name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(name, path="/", samesite="lax", secure=secure)


@router.get("/csrf")
def get_csrf_token(response: Response) -> dict[str, str]:
    """Seed a pre-auth CSRF double-submit cookie for the login request.

    Sets a random token in the readable CSRF cookie; the client echoes it in
    the X-CSRF-Token header on login. This protects login from cross-site
    request forgery without needing an existing session.
    """
    token = auth.generate_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
    )
    return {"csrf_token": token}


@router.post("/login")
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE_NAME),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> CurrentUser:
    """Authenticate with username/password and start a session.

    Returns a generic 401 for unknown users, wrong passwords and inactive
    accounts (no user enumeration). The raw session token is only set as a
    cookie. CSRF: the Origin is always checked and, when a pre-auth CSRF cookie
    is present, the header must match it (double-submit).
    """
    _check_origin(request)
    if csrf_cookie is not None:
        if not csrf_header or not _constant_time_equal(csrf_header, csrf_cookie):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token missing or invalid.",
            )

    # Pre-checks BEFORE any password hashing, so an already-blocked caller is
    # refused without spending PBKDF2 work. These are read-only (no increment):
    # the authoritative counting still happens in `enforce` below. Both keys
    # depend only on the client address and a normalised username digest, never
    # on whether the account exists, so a 429 here reveals nothing about
    # account existence. Windows expire, so no permanent lockout is possible.
    raise_if_blocked(login_limiter, login_key(request))
    raise_if_blocked(failed_login_limiter, failed_login_key(request, payload.username))

    user = auth.get_user_by_username(conn, payload.username)
    valid = (
        user is not None
        and bool(user["is_active"])
        and auth.verify_password(payload.password, user["password_hash"])
    )
    # Overall login limit applies to EVERY attempt, success or failure.
    enforce(login_limiter, login_key(request))
    if not valid:
        # Failed-attempt limit is keyed by the (normalised) username and the
        # direct client address, so changing username spelling cannot bypass
        # it. Successful logins never reach this branch and so never count as
        # a failure.
        enforce(failed_login_limiter, failed_login_key(request, payload.username))
        # Generic failure; never reveal which condition failed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR
        )

    issued = auth.create_session(conn, user["id"])
    _set_session_cookies(response, issued)
    logger.info("auth login: user_id=%s", user["id"])
    return CurrentUser(id=user["id"], username=user["username"], role=user["role"])


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    """Revoke the current session and clear cookies. Always 200.

    An invalid/absent session still returns 200 (and clears cookies). A live
    session requires a valid CSRF token; a forged logout without it is refused
    with 403 and does not revoke anything.
    """
    _check_origin(request)
    if session_token:
        context = auth.resolve_session(conn, session_token)
        if context is not None:
            if auth.verify_csrf(conn, context.session_id, csrf_header or ""):
                auth.revoke_session(conn, session_token)
            else:
                # Still clear cookies, but do not perform the revocation action
                # on a request that failed CSRF (return 403).
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF token missing or invalid.",
                )
    _clear_session_cookies(response)
    return {"status": "logged_out"}


@router.get("/me", response_model=CurrentUser)
def me(context: auth.SessionContext = Depends(require_user)) -> CurrentUser:
    """Return safe public fields for the verified caller."""
    return CurrentUser(id=context.user_id, username=context.username, role=context.role)


def _constant_time_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
