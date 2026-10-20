"""Bootstrap CLI behavior tests (Step 6.2C-1).

Focus: the bootstrap must never overwrite an existing user accidentally, must
never surface secrets, and must use isolated temporary databases.
"""

import pytest

from app import auth
from scripts.create_user import BootstrapError, bootstrap_user

TEST_ITERATIONS = 1000


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch) -> None:
    monkeypatch.setenv("PS_AUTH_PBKDF2_ITERATIONS", str(TEST_ITERATIONS))


def test_bootstrap_creates_user(conn) -> None:
    stored = bootstrap_user(
        conn, username="operator", password="a-strong-password-1"
    )
    assert stored == "operator"
    row = auth.get_user_by_username(conn, "operator")
    assert row is not None
    assert "a-strong-password-1" not in row["password_hash"]


def test_bootstrap_refuses_to_overwrite_without_rotate(conn) -> None:
    bootstrap_user(conn, username="operator", password="a-strong-password-1")
    original = auth.get_user_by_username(conn, "operator")["password_hash"]

    with pytest.raises(BootstrapError):
        bootstrap_user(conn, username="operator", password="a-different-password-2")

    # The stored hash is unchanged — nothing was overwritten.
    assert auth.get_user_by_username(conn, "operator")["password_hash"] == original


def test_bootstrap_rotate_replaces_password_deliberately(conn) -> None:
    bootstrap_user(conn, username="operator", password="a-strong-password-1")
    bootstrap_user(
        conn, username="operator", password="a-different-password-2", rotate=True
    )
    row = auth.get_user_by_username(conn, "operator")
    assert auth.verify_password("a-different-password-2", row["password_hash"])
    assert not auth.verify_password("a-strong-password-1", row["password_hash"])


def test_bootstrap_error_does_not_contain_password(conn) -> None:
    secret = "a-strong-password-1"
    bootstrap_user(conn, username="operator", password=secret)
    with pytest.raises(BootstrapError) as excinfo:
        bootstrap_user(conn, username="operator", password=secret)
    assert secret not in str(excinfo.value)


def test_bootstrap_rejects_short_password(conn) -> None:
    with pytest.raises(BootstrapError):
        bootstrap_user(conn, username="operator", password="short")


# --- strict single-user policy ----------------------------------------------


def test_bootstrap_refuses_second_distinct_username(conn) -> None:
    bootstrap_user(conn, username="operator", password="a-strong-password-1")
    with pytest.raises(BootstrapError):
        bootstrap_user(conn, username="intruder", password="a-strong-password-2")
    # The first account is untouched and no second row was created.
    assert auth.count_users(conn) == 1
    assert auth.get_user_by_username(conn, "intruder") is None


def test_second_user_refusal_mentions_no_secret(conn) -> None:
    bootstrap_user(conn, username="operator", password="a-strong-password-1")
    secret = "a-strong-password-2"
    with pytest.raises(BootstrapError) as excinfo:
        bootstrap_user(conn, username="intruder", password=secret)
    message = str(excinfo.value)
    assert secret not in message
    assert "a-strong-password-1" not in message


def test_bootstrap_leaves_existing_multi_account_db_unchanged(conn) -> None:
    # Simulate a legacy database that already has two accounts (bypassing the
    # policy, e.g. created before this guard existed or edited directly).
    auth.create_user(conn, username="legacy-a", password="a-strong-password-1")
    auth.create_user(conn, username="legacy-b", password="a-strong-password-2")
    conn.commit()
    before = auth.count_users(conn)

    with pytest.raises(BootstrapError):
        bootstrap_user(conn, username="third", password="a-strong-password-3")

    # Nothing added, removed, deactivated or renamed.
    assert auth.count_users(conn) == before
    assert auth.get_user_by_username(conn, "legacy-a") is not None
    assert auth.get_user_by_username(conn, "legacy-b") is not None
    assert auth.get_user_by_username(conn, "third") is None


def test_rotation_still_works_with_existing_single_account(conn) -> None:
    bootstrap_user(conn, username="operator", password="a-strong-password-1")
    # A different username is still refused, but the same username rotates.
    with pytest.raises(BootstrapError):
        bootstrap_user(conn, username="other", password="a-strong-password-2")
    bootstrap_user(
        conn, username="operator", password="a-strong-password-3", rotate=True
    )
    row = auth.get_user_by_username(conn, "operator")
    assert auth.verify_password("a-strong-password-3", row["password_hash"])
    assert auth.count_users(conn) == 1
