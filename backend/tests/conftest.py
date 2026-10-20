"""Shared fixtures: every test gets its own temporary SQLite database.

The default `client` fixture is AUTHENTICATED: it creates a test user, logs in
through the real /auth/login endpoint and carries the session cookie plus the
CSRF header on every request. `anon_client` is the same app with no session, for
explicit unauthenticated negative tests. Tests keep using `client` unchanged for
business behavior; authentication tests use `anon_client` or their own sessions.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import auth
from app.db import connect
from app.main import app
from app.ratelimit_config import reset_all_limiters
from app.security import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

TEST_USERNAME = "test-operator"
TEST_PASSWORD = "a-strong-password-1"


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """Reset shared limiter state before each test.

    The limiters are application-scoped and persist across requests within a
    process; without this, one test's login attempts would consume the login
    budget of the next. Tests that exercise limiting set their own state.
    """
    reset_all_limiters()
    yield
    reset_all_limiters()


@pytest.fixture()
def _isolated_env() -> Iterator[MonkeyPatch]:
    """Environment isolation on a private MonkeyPatch instance.

    Deliberately NOT the shared `monkeypatch` fixture: some tests call
    `monkeypatch.undo()`, which would otherwise wipe PS_APP_DB mid-test.
    """
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture()
def db_path(tmp_path, _isolated_env) -> Iterator:
    """Point PS_APP_DB at a unique temp file so tests never touch dev data."""
    path = tmp_path / "test.db"
    _isolated_env.setenv("PS_APP_DB", str(path))
    # Keep hashing fast in tests; production default remains 600,000.
    _isolated_env.setenv("PS_AUTH_PBKDF2_ITERATIONS", "1000")
    yield path


@pytest.fixture()
def conn(db_path) -> Iterator:
    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture()
def docs_dir(db_path, tmp_path, _isolated_env) -> Iterator:
    """Isolated storage directory so tests never touch backend/data/documents."""
    path = tmp_path / "documents"
    _isolated_env.setenv("PS_DOCS_DIR", str(path))
    yield path


def make_user(
    conn, username: str = TEST_USERNAME, password: str = TEST_PASSWORD,
    role: str = "user", is_active: bool = True,
) -> int:
    """Create a user directly in the temp database."""
    row = auth.create_user(
        conn, username=username, password=password, role=role, is_active=is_active
    )
    conn.commit()
    return row["id"]


def login_client(test_client: TestClient, username: str, password: str) -> None:
    """Log in through the real endpoint and wire the CSRF header."""
    csrf = test_client.get("/auth/csrf").json()["csrf_token"]
    response = test_client.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers={CSRF_HEADER_NAME: csrf},
    )
    assert response.status_code == 200, response.text
    cookie_csrf = test_client.cookies.get(CSRF_COOKIE_NAME)
    assert cookie_csrf
    # Unsafe requests must echo the session-bound CSRF token.
    test_client.headers[CSRF_HEADER_NAME] = cookie_csrf


@pytest.fixture()
def anon_client(db_path) -> Iterator[TestClient]:
    """A client with no session, for unauthenticated negative tests."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client(db_path, conn) -> Iterator[TestClient]:
    """Authenticated client: real login, session cookie + CSRF header."""
    user_id = make_user(conn)
    del user_id  # created for side effect
    with TestClient(app) as test_client:
        login_client(test_client, TEST_USERNAME, TEST_PASSWORD)
        yield test_client
