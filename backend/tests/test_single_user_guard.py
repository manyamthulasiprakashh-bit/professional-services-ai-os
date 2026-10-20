"""Runtime single-user policy guard (Step 7.1B).

The application enforces at most one ACTIVE account at startup. A database with
zero accounts is allowed (first-time setup); exactly one active account is
allowed; more than one active account fails closed. The guard is read-only: it
never deletes, deactivates or alters accounts.

All tests use isolated temporary databases via the shared `db_path`/`conn`
fixtures; the real local database is never touched.
"""

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import SingleUserPolicyError, verify_single_user_policy
from app.main import app


def _add_user(conn, username: str, *, active: bool = True) -> None:
    auth.create_user(
        conn, username=username, password="a-strong-password-1", is_active=active
    )
    conn.commit()


# --- policy function (unit) -------------------------------------------------


def test_zero_users_is_allowed(conn) -> None:
    assert auth.count_users(conn) == 0
    verify_single_user_policy(conn)  # does not raise


def test_exactly_one_active_user_is_allowed(conn) -> None:
    _add_user(conn, "operator")
    verify_single_user_policy(conn)  # does not raise


def test_multiple_active_users_fail_closed(conn) -> None:
    _add_user(conn, "operator")
    _add_user(conn, "second")
    with pytest.raises(SingleUserPolicyError):
        verify_single_user_policy(conn)


def test_inactive_accounts_do_not_count(conn) -> None:
    # One active + one inactive: within policy (only active accounts count).
    _add_user(conn, "operator", active=True)
    _add_user(conn, "retired", active=False)
    verify_single_user_policy(conn)  # does not raise


def test_two_inactive_accounts_are_allowed(conn) -> None:
    _add_user(conn, "retired-a", active=False)
    _add_user(conn, "retired-b", active=False)
    verify_single_user_policy(conn)  # does not raise


def test_guard_is_read_only(conn) -> None:
    _add_user(conn, "operator")
    _add_user(conn, "second")
    before = conn.execute("SELECT id, is_active FROM users ORDER BY id").fetchall()
    with pytest.raises(SingleUserPolicyError):
        verify_single_user_policy(conn)
    after = conn.execute("SELECT id, is_active FROM users ORDER BY id").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


def test_policy_error_message_contains_no_secrets(conn) -> None:
    _add_user(conn, "operator")
    _add_user(conn, "second")
    with pytest.raises(SingleUserPolicyError) as excinfo:
        verify_single_user_policy(conn)
    message = str(excinfo.value)
    assert "operator" not in message
    assert "second" not in message
    assert "a-strong-password-1" not in message


# --- startup boundary (integration) -----------------------------------------


def test_startup_succeeds_with_one_active_user(db_path, conn) -> None:
    _add_user(conn, "operator")
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200


def test_startup_succeeds_with_zero_users(db_path) -> None:
    # No user created: first-time setup must still be able to start the app.
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200


def test_startup_fails_closed_with_multiple_active_users(db_path, conn) -> None:
    _add_user(conn, "operator")
    _add_user(conn, "second")
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - any startup failure
        with TestClient(app):
            pass
    assert "single-user" in str(excinfo.value).lower() or isinstance(
        excinfo.value, SingleUserPolicyError
    )


def test_startup_does_not_modify_legacy_multi_account_db(db_path, conn) -> None:
    _add_user(conn, "legacy-a")
    _add_user(conn, "legacy-b")
    before = conn.execute(
        "SELECT id, username, is_active FROM users ORDER BY id"
    ).fetchall()
    with pytest.raises(Exception):  # noqa: B017
        with TestClient(app):
            pass
    after = conn.execute(
        "SELECT id, username, is_active FROM users ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
