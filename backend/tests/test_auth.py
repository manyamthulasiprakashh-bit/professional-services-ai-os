"""Authentication foundation tests (Step 6.2C-1).

All tests use isolated temporary databases and test credentials. The work
factor is lowered via PS_AUTH_PBKDF2_ITERATIONS so tests stay fast; the
production default (600,000) is asserted separately without hashing.
"""

import sqlite3

import pytest

from app import auth
from app.db import SCHEMA_SQL, connect

TEST_ITERATIONS = 1000


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch) -> None:
    monkeypatch.setenv("PS_AUTH_PBKDF2_ITERATIONS", str(TEST_ITERATIONS))


# --- Password hashing -------------------------------------------------------


def test_default_work_factor_is_600k() -> None:
    assert auth.DEFAULT_PBKDF2_ITERATIONS == 600_000


def test_hash_then_verify_succeeds() -> None:
    encoded = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", encoded) is True


def test_verify_wrong_password_fails() -> None:
    encoded = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("wrong password", encoded) is False


def test_plaintext_is_not_stored_in_hash() -> None:
    secret = "unique-plaintext-secret-value"
    encoded = auth.hash_password(secret)
    assert secret not in encoded
    assert encoded.startswith("pbkdf2_sha256$")


def test_identical_passwords_get_distinct_salts_and_hashes() -> None:
    first = auth.hash_password("same password for both")
    second = auth.hash_password("same password for both")
    assert first != second
    assert first.split("$")[2] != second.split("$")[2]
    assert auth.verify_password("same password for both", first)
    assert auth.verify_password("same password for both", second)


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$only-three$parts",
        "pbkdf2_sha256$abc$deadbeef$deadbeef",  # non-integer iterations
        "pbkdf2_sha256$0$deadbeef$deadbeef",  # non-positive iterations
        "pbkdf2_sha256$1000$nothex$deadbeef",  # non-hex salt
        "pbkdf2_sha256$1000$deadbeef$nothex",  # non-hex hash
        "bcrypt$1000$deadbeef$deadbeef",  # unknown algorithm
    ],
)
def test_malformed_stored_hash_fails_closed(malformed: str) -> None:
    assert auth.verify_password("anything", malformed) is False


def test_short_password_rejected() -> None:
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_blank_username_rejected() -> None:
    with pytest.raises(ValueError):
        auth.validate_username("   ")


# --- Users ------------------------------------------------------------------


def test_create_user_stores_hash_not_plaintext(conn: sqlite3.Connection) -> None:
    row = auth.create_user(conn, username="alice", password="a-strong-password-1")
    assert row["username"] == "alice"
    assert row["is_active"] == 1
    assert row["role"] == "user"
    assert "a-strong-password-1" not in row["password_hash"]


def test_duplicate_username_raises_integrity_error(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, username="alice", password="a-strong-password-1")
    with pytest.raises(sqlite3.IntegrityError):
        auth.create_user(conn, username="alice", password="another-password-2")


def test_get_user_by_username_is_case_insensitive(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, username="alice", password="a-strong-password-1")
    assert auth.get_user_by_username(conn, "ALICE") is not None
    assert auth.get_user_by_username(conn, "nobody") is None


# --- Sessions ---------------------------------------------------------------


def _make_user(conn: sqlite3.Connection, active: bool = True) -> int:
    row = auth.create_user(
        conn, username="alice", password="a-strong-password-1", is_active=active
    )
    return row["id"]


def test_session_stores_only_token_hash(conn: sqlite3.Connection) -> None:
    user_id = _make_user(conn)
    raw = auth.create_session(conn, user_id).token
    stored = conn.execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
    assert stored != raw
    assert stored == auth.hash_token(raw)
    assert raw not in stored


def test_session_returns_csrf_token_and_stores_only_its_hash(
    conn: sqlite3.Connection,
) -> None:
    user_id = _make_user(conn)
    issued = auth.create_session(conn, user_id)
    row = conn.execute("SELECT csrf_token_hash FROM sessions").fetchone()
    assert row["csrf_token_hash"] == auth.hash_token(issued.csrf_token)
    assert issued.csrf_token not in row["csrf_token_hash"]
    assert auth.verify_csrf(conn, 1, issued.csrf_token) is True
    assert auth.verify_csrf(conn, 1, "wrong") is False
    assert auth.verify_csrf(conn, 1, "") is False


def test_resolve_valid_session(conn: sqlite3.Connection) -> None:
    user_id = _make_user(conn)
    raw = auth.create_session(conn, user_id).token
    ctx = auth.resolve_session(conn, raw)
    assert ctx is not None
    assert ctx.user_id == user_id
    assert ctx.username == "alice"
    assert ctx.role == "user"


def test_resolve_rejects_missing_and_tampered_tokens(conn: sqlite3.Connection) -> None:
    user_id = _make_user(conn)
    raw = auth.create_session(conn, user_id).token
    assert auth.resolve_session(conn, "") is None
    assert auth.resolve_session(conn, "not-a-real-token") is None
    assert auth.resolve_session(conn, raw + "x") is None
    assert auth.resolve_session(conn, raw[:-1]) is None


def test_expired_session_rejected(conn: sqlite3.Connection) -> None:
    user_id = _make_user(conn)
    raw = auth.create_session(conn, user_id, ttl_seconds=-1).token
    assert auth.resolve_session(conn, raw) is None


def test_revoked_session_rejected(conn: sqlite3.Connection) -> None:
    user_id = _make_user(conn)
    raw = auth.create_session(conn, user_id).token
    assert auth.revoke_session(conn, raw) is True
    assert auth.resolve_session(conn, raw) is None
    assert auth.revoke_session(conn, raw) is False  # already revoked


def test_inactive_user_cannot_create_or_resolve_session(
    conn: sqlite3.Connection,
) -> None:
    user_id = _make_user(conn, active=False)
    with pytest.raises(PermissionError):
        auth.create_session(conn, user_id)

    # A session created while active must stop working once the user is disabled.
    active_id = auth.create_user(
        conn, username="bob", password="a-strong-password-2"
    )["id"]
    raw = auth.create_session(conn, active_id).token
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (active_id,))
    assert auth.resolve_session(conn, raw) is None


def test_create_session_unknown_user_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(LookupError):
        auth.create_session(conn, 9999)


def test_delete_expired_sessions_cleans_rows(conn: sqlite3.Connection) -> None:
    user_id = _make_user(conn)
    auth.create_session(conn, user_id, ttl_seconds=-1)
    raw = auth.create_session(conn, user_id).token
    auth.revoke_session(conn, raw)
    removed = auth.delete_expired_sessions(conn)
    assert removed == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0


# --- Migration --------------------------------------------------------------


def test_schema_sql_does_not_drop_tables() -> None:
    lowered = SCHEMA_SQL.lower()
    assert "drop table" not in lowered
    assert "delete from" not in lowered
    assert "alter table" not in lowered


def test_repeatable_migration_on_legacy_database(db_path) -> None:
    """A pre-auth database with business data gains auth tables without loss."""
    import json

    legacy = connect(db_path)  # first connect applies the full schema
    # Simulate a legacy database: business + proposal + audit rows exist.
    legacy.execute(
        "INSERT INTO clients (name, email, created_at) VALUES (?, ?, ?)",
        ("Acme Legal", "ops@acme.test", "2026-01-01T00:00:00+00:00"),
    )
    legacy.execute(
        "INSERT INTO matters (client_id, title, status, created_at) VALUES (1, ?, ?, ?)",
        ("Harbor", "open", "2026-01-01T00:00:00+00:00"),
    )
    legacy.execute(
        "INSERT INTO action_proposals (proposal_id, action_type, status, payload_json,"
        " payload_hash, evidence_json, model, created_at, updated_at)"
        " VALUES (?, 'create_task', 'executed', ?, ?, ?, ?, ?, ?)",
        ("prop-legacy", json.dumps({}), "hash", json.dumps([]), "qwen3:4b",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    legacy.execute(
        "INSERT INTO action_audit (proposal_id, event, detail, created_at)"
        " VALUES ('prop-legacy', 'executed', 'task_id=1', '2026-01-01T00:00:00+00:00')"
    )
    legacy.commit()
    legacy.close()

    # Re-open: migration must run again without error and preserve all rows.
    again = connect(db_path)
    try:
        assert again.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"] == 1
        assert again.execute("SELECT COUNT(*) AS n FROM matters").fetchone()["n"] == 1
        proposals = again.execute(
            "SELECT status FROM action_proposals"
        ).fetchone()
        assert proposals["status"] == "executed"
        assert again.execute(
            "SELECT COUNT(*) AS n FROM action_audit"
        ).fetchone()["n"] == 1
        # Auth tables now exist and are usable.
        assert again.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        assert again.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
    finally:
        again.close()


def test_migration_is_idempotent_across_many_connects(db_path) -> None:
    for _ in range(3):
        conn = connect(db_path)
        conn.close()
