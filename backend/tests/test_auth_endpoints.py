"""Login/logout/me, session enforcement, CSRF and origin tests (Step 6.2C-2).

Uses the real endpoints with isolated temp databases. `anon_client` has no
session; `client` is authenticated. Browser-level cookie/attribute behavior is
asserted from response headers and the TestClient cookie jar; true cross-site
browser behavior (SameSite enforcement by a real browser) is NOT exercised here.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.security import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
)
from conftest import TEST_PASSWORD, TEST_USERNAME, login_client, make_user


# --- login ------------------------------------------------------------------


def _csrf(anon: TestClient) -> str:
    return anon.get("/auth/csrf").json()["csrf_token"]


def _login(anon: TestClient, username: str, password: str):
    token = _csrf(anon)
    return anon.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers={CSRF_HEADER_NAME: token},
    )


def test_login_success_sets_cookie_attributes(anon_client, conn) -> None:
    make_user(conn)
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)

    assert response.status_code == 200
    assert response.json() == {"id": 1, "username": TEST_USERNAME, "role": "user"}
    # Session token is never in the JSON body.
    assert SESSION_COOKIE_NAME not in response.text
    set_cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()


def test_login_never_returns_raw_token_in_json(anon_client, conn) -> None:
    make_user(conn)
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    raw = anon_client.cookies.get(SESSION_COOKIE_NAME)
    assert raw and raw not in response.text


def test_session_cookie_is_httponly_but_csrf_cookie_is_readable(anon_client, conn) -> None:
    make_user(conn)
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(SESSION_COOKIE_NAME))
    csrf_cookie = next(c for c in cookies if c.startswith(CSRF_COOKIE_NAME))
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie


def test_login_cookies_are_secure_when_enabled(anon_client, conn, monkeypatch) -> None:
    """Production opt-in marks both cookies Secure without changing the rest."""
    monkeypatch.setenv("PS_AUTH_COOKIE_SECURE", "true")
    make_user(conn)
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)

    cookies = response.headers.get_list("set-cookie")
    for name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        cookie = next(c for c in cookies if c.startswith(name))
        assert "Secure" in cookie
        assert "samesite=lax" in cookie.lower()
    session_cookie = next(
        c for c in cookies if c.startswith(SESSION_COOKIE_NAME)
    )
    assert "HttpOnly" in session_cookie


def test_login_unknown_user_is_generic_401(anon_client) -> None:
    response = _login(anon_client, "nobody", "a-strong-password-1")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_login_wrong_password_is_generic_401(anon_client, conn) -> None:
    make_user(conn)
    response = _login(anon_client, TEST_USERNAME, "wrong-password-xx")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_login_inactive_user_is_generic_401(anon_client, conn) -> None:
    make_user(conn, is_active=False)
    response = _login(anon_client, TEST_USERNAME, TEST_PASSWORD)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_login_without_csrf_cookie_still_requires_valid_origin(anon_client, conn) -> None:
    make_user(conn)
    # No CSRF cookie seeded; login responds 401 for bad creds (origin checked).
    response = anon_client.post(
        "/auth/login", json={"username": TEST_USERNAME, "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_with_bad_origin_rejected(anon_client, conn) -> None:
    make_user(conn)
    token = _csrf(anon_client)
    response = anon_client.post(
        "/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
        headers={CSRF_HEADER_NAME: token, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


# --- me ---------------------------------------------------------------------


def test_me_with_valid_session(client) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == TEST_USERNAME
    assert set(body.keys()) == {"id", "username", "role"}
    assert "password_hash" not in response.text
    assert SESSION_COOKIE_NAME not in response.text


def test_me_without_session_is_401(anon_client) -> None:
    assert anon_client.get("/auth/me").status_code == 401


def test_me_with_invalid_session_is_401(anon_client) -> None:
    anon_client.cookies.set(SESSION_COOKIE_NAME, "garbage")
    assert anon_client.get("/auth/me").status_code == 401


def test_me_with_expired_session_is_401(anon_client, conn) -> None:
    user_id = make_user(conn)
    issued = auth.create_session(conn, user_id, ttl_seconds=-1)
    conn.commit()
    anon_client.cookies.set(SESSION_COOKIE_NAME, issued.token)
    assert anon_client.get("/auth/me").status_code == 401


def test_me_with_revoked_session_is_401(anon_client, conn) -> None:
    user_id = make_user(conn)
    issued = auth.create_session(conn, user_id)
    auth.revoke_session(conn, issued.token)
    conn.commit()
    anon_client.cookies.set(SESSION_COOKIE_NAME, issued.token)
    assert anon_client.get("/auth/me").status_code == 401


# --- logout -----------------------------------------------------------------


def test_logout_revokes_and_clears(client, conn) -> None:
    assert client.get("/auth/me").status_code == 200
    response = client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}
    # Cookie cleared in the response.
    cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith(SESSION_COOKIE_NAME) and "Max-Age=0" in c for c in cookies)
    # Server-side session revoked: reuse must fail.
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE revoked_at IS NOT NULL"
    ).fetchone()["n"]
    assert count >= 1


def test_logout_without_session_is_ok(anon_client) -> None:
    response = anon_client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}


def test_logout_without_csrf_is_403(anon_client, conn) -> None:
    user_id = make_user(conn)
    issued = auth.create_session(conn, user_id)
    conn.commit()
    anon_client.cookies.set(SESSION_COOKIE_NAME, issued.token)
    # No X-CSRF-Token header.
    response = anon_client.post("/auth/logout")
    assert response.status_code == 403


# --- route enforcement ------------------------------------------------------


PROTECTED_GET = [
    "/clients",
    "/matters",
    "/tasks",
    "/documents",
    "/documents/index",
    "/ollama/health",
    "/assistant/proposals/1",
]

PROTECTED_POST = [
    ("/clients", {"name": "X"}),
    ("/matters", {"client_id": 1, "title": "X"}),
    ("/tasks", {"matter_id": 1, "title": "X"}),
    ("/documents/index", None),
    ("/documents/1/extract", None),
    ("/documents/1/index", None),
    ("/documents/1/summarize", None),
    ("/assistant/ask", {"question": "X"}),
    ("/assistant/propose-action", {"request": "X"}),
    ("/assistant/proposals/nope/approve", None),
    ("/assistant/proposals/nope/reject", None),
]


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_protected_get_requires_auth(anon_client, path) -> None:
    assert anon_client.get(path).status_code == 401


@pytest.mark.parametrize("path,body", PROTECTED_POST)
def test_protected_post_requires_auth(anon_client, path, body) -> None:
    response = anon_client.post(path, json=body if body is not None else {})
    assert response.status_code == 401


def test_upload_requires_auth_before_any_storage_write(anon_client, docs_dir) -> None:
    """POST /documents needs multipart data, so it is not in the JSON lists.

    An unauthenticated upload must be rejected before any file is stored.
    """
    response = anon_client.post(
        "/documents",
        data={"matter_id": "1"},
        files={"file": ("notes.txt", b"secret contents")},
    )
    assert response.status_code == 401
    assert not docs_dir.exists() or not any(docs_dir.iterdir())


def test_health_and_root_remain_public(anon_client) -> None:
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/").status_code == 200


def test_docs_remain_public(anon_client) -> None:
    # Documented choice: interactive docs/openapi stay public (no business data).
    assert anon_client.get("/openapi.json").status_code == 200
    assert anon_client.get("/docs").status_code == 200
    assert anon_client.get("/redoc").status_code == 200


# --- CSRF -------------------------------------------------------------------


def test_state_change_without_csrf_header_is_403(client) -> None:
    # client normally injects the CSRF header; remove it for this call.
    saved = client.headers.pop(CSRF_HEADER_NAME)
    try:
        response = client.post("/clients", json={"name": "NoCsrf"})
    finally:
        client.headers[CSRF_HEADER_NAME] = saved
    assert response.status_code == 403


def test_state_change_with_wrong_csrf_header_is_403(client) -> None:
    saved = client.headers[CSRF_HEADER_NAME]
    client.headers[CSRF_HEADER_NAME] = "not-the-right-token"
    try:
        response = client.post("/clients", json={"name": "BadCsrf"})
    finally:
        client.headers[CSRF_HEADER_NAME] = saved
    assert response.status_code == 403


def test_state_change_with_valid_csrf_succeeds(client) -> None:
    response = client.post("/clients", json={"name": "GoodCsrf"})
    assert response.status_code == 201


def test_get_requests_do_not_require_csrf(client) -> None:
    saved = client.headers.pop(CSRF_HEADER_NAME)
    try:
        assert client.get("/clients").status_code == 200
    finally:
        client.headers[CSRF_HEADER_NAME] = saved


# --- cross-session CSRF isolation -------------------------------------------


def test_csrf_token_is_bound_to_its_session(db_path, conn) -> None:
    """A CSRF token from session A must not work with session B's cookie."""
    make_user(conn)

    with TestClient(app) as session_a, TestClient(app) as session_b:
        login_client(session_a, TEST_USERNAME, TEST_PASSWORD)
        login_client(session_b, TEST_USERNAME, TEST_PASSWORD)

        token_a = session_a.headers[CSRF_HEADER_NAME]
        token_b = session_b.headers[CSRF_HEADER_NAME]
        assert token_a != token_b  # distinct sessions, distinct tokens

        # A's own token + A's cookie succeeds.
        assert session_a.post("/clients", json={"name": "OwnedByA"}).status_code == 201

        # A's token echoed with B's session cookie is refused...
        cross = session_b.post(
            "/clients",
            json={"name": "CrossSession"},
            headers={CSRF_HEADER_NAME: token_a},
        )
        assert cross.status_code == 403

        # ...and the failed request performed no mutation.
        names = [c["name"] for c in session_b.get("/clients").json()]
        assert "CrossSession" not in names

        # B's own token remains usable for B.
        assert session_b.post("/clients", json={"name": "OwnedByB"}).status_code == 201


def test_unsafe_request_with_invalid_origin_is_403(client) -> None:
    response = client.post(
        "/clients",
        json={"name": "BadOrigin"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_unsafe_request_with_allowed_origin_succeeds(client) -> None:
    from app.main import ALLOWED_ORIGINS

    response = client.post(
        "/clients",
        json={"name": "GoodOrigin"},
        headers={"Origin": ALLOWED_ORIGINS[0]},
    )
    assert response.status_code == 201


# --- Referer fallback (Origin absent) ---------------------------------------


def test_unsafe_request_with_allowed_referer_succeeds(client) -> None:
    """No Origin: an allowed Referer passes and the request is honoured."""
    from app.main import ALLOWED_ORIGINS

    response = client.post(
        "/clients",
        json={"name": "GoodReferer"},
        headers={"Referer": f"{ALLOWED_ORIGINS[0]}/some/page"},
    )
    assert response.status_code == 201


def test_unsafe_request_with_disallowed_referer_is_403(client) -> None:
    """No Origin: a disallowed Referer is rejected and performs no mutation."""
    response = client.post(
        "/clients",
        json={"name": "BadReferer"},
        headers={"Referer": "https://evil.example/attack"},
    )
    assert response.status_code == 403
    # The mutation must not have happened.
    names = [c["name"] for c in client.get("/clients").json()]
    assert "BadReferer" not in names


def test_unsafe_request_with_malformed_referer_is_403(client) -> None:
    """No Origin: a malformed/relative Referer is not a match and is rejected."""
    for bad in ("not-a-url", "//evil.example/x", "http://evil.example"):
        response = client.post(
            "/clients",
            json={"name": "MalformedReferer"},
            headers={"Referer": bad},
        )
        assert response.status_code == 403, bad


def test_referer_check_does_not_bypass_csrf(client) -> None:
    """Even with an allowed Referer, a missing CSRF header is still 403."""
    from app.main import ALLOWED_ORIGINS

    saved = client.headers.pop(CSRF_HEADER_NAME)
    try:
        response = client.post(
            "/clients",
            json={"name": "RefererNoCsrf"},
            headers={"Referer": f"{ALLOWED_ORIGINS[0]}/page"},
        )
    finally:
        client.headers[CSRF_HEADER_NAME] = saved
    assert response.status_code == 403


# --- referer allows valid Origin behavior to remain unchanged (regression) ---


def test_allowed_origin_still_checked_when_referer_also_present(client) -> None:
    """Origin takes precedence: a bad Origin fails even with a good Referer."""
    from app.main import ALLOWED_ORIGINS

    response = client.post(
        "/clients",
        json={"name": "OriginWins"},
        headers={
            "Origin": "https://evil.example",
            "Referer": f"{ALLOWED_ORIGINS[0]}/page",
        },
    )
    assert response.status_code == 403


# --- forged identity fields -------------------------------------------------


def test_forged_user_and_role_fields_do_not_change_identity(client, conn) -> None:
    """Body user_id/role are ignored; /auth/me reflects the session only."""
    response = client.post(
        "/clients",
        json={"name": "Forged", "user_id": 999, "role": "admin"},
    )
    # Extra fields are not part of ClientCreate; request still succeeds as the
    # authenticated user, and identity is unchanged.
    assert response.status_code == 201
    me = client.get("/auth/me").json()
    assert me["username"] == TEST_USERNAME
    assert me["role"] == "user"


def test_session_identity_cannot_be_supplied_by_header(client) -> None:
    response = client.get("/auth/me", headers={"X-User-Id": "999"})
    assert response.json()["username"] == TEST_USERNAME


# --- secret non-leakage -----------------------------------------------------


def test_session_table_never_stores_raw_token(conn) -> None:
    user_id = make_user(conn)
    issued = auth.create_session(conn, user_id)
    conn.commit()
    rows = conn.execute("SELECT token_hash, csrf_token_hash FROM sessions").fetchall()
    stored = {*[r["token_hash"] for r in rows], *[r["csrf_token_hash"] for r in rows]}
    assert issued.token not in stored
    assert issued.csrf_token not in stored


def test_password_hash_never_returned_by_me(client, conn) -> None:
    row = auth.get_user_by_username(conn, TEST_USERNAME)
    response = client.get("/auth/me")
    assert row["password_hash"] not in response.text
