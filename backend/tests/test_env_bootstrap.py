"""Environment-driven initial-user bootstrap (PS_BOOTSTRAP_*).

Focus:
- a configured PBKDF2 hash creates a user ONLY when the database has zero users;
- an existing account is never overwritten, even when bootstrap is configured;
- bootstrap runs before the single-user startup guard and cannot add a second
  account to a legacy multi-account database;
- secrets (username, password, hash) never appear in errors or logs;
- the exact ``hash_password`` / ``verify_password`` format is reused.

All tests use isolated temporary databases; the real local database is never
touched.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.bootstrap import (
    BOOTSTRAP_PASSWORD_ENV,
    BOOTSTRAP_PASSWORD_HASH_ENV,
    BOOTSTRAP_USERNAME_ENV,
    BootstrapError,
    bootstrap_from_env,
)
from app.main import app
from app.security import CSRF_HEADER_NAME

GOOD_PASSWORD = "a-strong-password-1"


def _hash(password: str = GOOD_PASSWORD) -> str:
    return auth.hash_password(password)


# --- hash format reuse ------------------------------------------------------


def test_generated_hash_round_trips_and_is_valid() -> None:
    encoded = _hash()
    auth.validate_password_hash(encoded)  # does not raise
    assert auth.verify_password(GOOD_PASSWORD, encoded)
    assert not auth.verify_password("wrong-password-1234", encoded)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$0$aa$bb",
        "pbkdf2_sha256$1000$nothex$bb",
        "pbkdf2_sha256$1000$aa$nothex",
        "pbkdf2_sha256$1000$$",
        "md5$1000$aa$bb",
    ],
)
def test_invalid_hashes_are_rejected(conn, bad: str) -> None:
    with pytest.raises(ValueError):
        auth.validate_password_hash(bad)
    with pytest.raises(ValueError):
        auth.create_user_with_hash(conn, username="operator", password_hash=bad)
    assert auth.count_users(conn) == 0


# --- bootstrap_from_env (unit) ----------------------------------------------


def test_env_bootstrap_creates_first_user_from_hash(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, _hash())
    assert bootstrap_from_env(conn) == "operator"
    assert auth.count_users(conn) == 1
    row = auth.get_user_by_username(conn, "operator")
    assert auth.verify_password(GOOD_PASSWORD, row["password_hash"])


def test_env_bootstrap_noop_when_unset(conn) -> None:
    assert bootstrap_from_env(conn) is None
    assert auth.count_users(conn) == 0


def test_env_bootstrap_hash_requires_zero_users(conn, _isolated_env) -> None:
    auth.create_user(conn, username="existing", password="existing-password-1")
    conn.commit()
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, _hash())
    assert bootstrap_from_env(conn) is None
    assert auth.count_users(conn) == 1


def test_env_bootstrap_never_overwrites_existing_account(conn, _isolated_env) -> None:
    auth.create_user(conn, username="operator", password=GOOD_PASSWORD)
    conn.commit()
    before = auth.get_user_by_username(conn, "operator")["password_hash"]

    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(
        BOOTSTRAP_PASSWORD_HASH_ENV, _hash("a-different-password-2")
    )
    assert bootstrap_from_env(conn) is None

    after = auth.get_user_by_username(conn, "operator")["password_hash"]
    assert after == before
    assert auth.verify_password(GOOD_PASSWORD, after)


def test_env_bootstrap_requires_username(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, _hash())
    with pytest.raises(BootstrapError):
        bootstrap_from_env(conn)
    assert auth.count_users(conn) == 0


def test_env_bootstrap_requires_a_credential(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    with pytest.raises(BootstrapError):
        bootstrap_from_env(conn)
    assert auth.count_users(conn) == 0


def test_env_bootstrap_rejects_invalid_hash(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, "not-a-valid-hash")
    with pytest.raises(BootstrapError):
        bootstrap_from_env(conn)
    assert auth.count_users(conn) == 0


def test_env_bootstrap_prefers_hash_over_password(conn, _isolated_env) -> None:
    hash_password_value = "hash-wins-password-1"
    plaintext_password = "plain-loses-password-1"
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(
        BOOTSTRAP_PASSWORD_HASH_ENV, _hash(hash_password_value)
    )
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_ENV, plaintext_password)
    assert bootstrap_from_env(conn) == "operator"
    row = auth.get_user_by_username(conn, "operator")
    assert auth.verify_password(hash_password_value, row["password_hash"])
    assert not auth.verify_password(plaintext_password, row["password_hash"])


def test_env_bootstrap_password_fallback(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_ENV, GOOD_PASSWORD)
    assert bootstrap_from_env(conn) == "operator"
    row = auth.get_user_by_username(conn, "operator")
    assert auth.verify_password(GOOD_PASSWORD, row["password_hash"])


def test_env_bootstrap_rejects_short_password(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_ENV, "short")
    with pytest.raises(BootstrapError):
        bootstrap_from_env(conn)
    assert auth.count_users(conn) == 0


def test_env_bootstrap_error_contains_no_secret(conn, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_ENV, "short")
    with pytest.raises(BootstrapError) as excinfo:
        bootstrap_from_env(conn)
    message = str(excinfo.value)
    assert "short" not in message
    assert "operator" not in message


def test_env_bootstrap_logs_no_secret(conn, _isolated_env, caplog) -> None:
    secret_password = "super-secret-password-9"
    secret_hash = _hash(secret_password)
    secret_username = "top-secret-user"
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, secret_username)
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, secret_hash)
    with caplog.at_level(logging.INFO):
        bootstrap_from_env(conn)
    assert secret_password not in caplog.text
    assert secret_hash not in caplog.text
    assert secret_username not in caplog.text


# --- startup integration ----------------------------------------------------


def test_startup_bootstraps_user_and_login_works(db_path, _isolated_env) -> None:
    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, _hash())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        csrf = client.get("/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/auth/login",
            json={"username": "operator", "password": GOOD_PASSWORD},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert response.status_code == 200, response.text


def test_startup_env_bootstrap_does_not_overwrite_existing(
    db_path, conn, _isolated_env
) -> None:
    auth.create_user(conn, username="operator", password=GOOD_PASSWORD)
    conn.commit()
    before = auth.get_user_by_username(conn, "operator")["password_hash"]

    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(
        BOOTSTRAP_PASSWORD_HASH_ENV, _hash("a-different-password-2")
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    after = auth.get_user_by_username(conn, "operator")["password_hash"]
    assert after == before
    assert auth.count_users(conn) == 1


def test_startup_bootstrap_runs_before_guard_and_adds_no_third_account(
    db_path, conn, _isolated_env
) -> None:
    auth.create_user(conn, username="legacy-a", password=GOOD_PASSWORD)
    auth.create_user(conn, username="legacy-b", password="a-strong-password-2")
    conn.commit()

    _isolated_env.setenv(BOOTSTRAP_USERNAME_ENV, "operator")
    _isolated_env.setenv(BOOTSTRAP_PASSWORD_HASH_ENV, _hash("a-strong-password-3"))
    with pytest.raises(Exception):  # noqa: B017 - any startup failure
        with TestClient(app):
            pass

    assert auth.count_users(conn) == 2
    assert auth.get_user_by_username(conn, "operator") is None


def test_startup_without_env_and_zero_users_still_starts(db_path, conn) -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert auth.count_users(conn) == 0
