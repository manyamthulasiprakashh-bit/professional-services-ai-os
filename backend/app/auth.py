"""Backend authentication foundation (Step 6.2C-1).

This module provides password hashing and opaque server-side session
primitives. It is DATA + LOGIC ONLY: it does not register routes, does not
protect any router, and imports no FastAPI. Route enforcement, login endpoints
and CSRF belong to later substeps.

Security properties:
- Passwords are hashed with PBKDF2-HMAC-SHA256, a unique random salt per
  password, and a documented work factor. Plaintext passwords are never stored.
- Session tokens are random (``secrets.token_urlsafe(32)``); only their
  SHA-256 hash is persisted, and the raw token is returned exactly once to the
  caller that will issue the cookie.
- Missing, invalid, expired or revoked sessions are rejected (fail closed).
- Tokens and passwords are never included in exceptions or logs.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# --- Password hashing -------------------------------------------------------

PBKDF2_ALGORITHM = "sha256"
DEFAULT_PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
HASH_BYTES = 32
# Encoded as: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
HASH_PREFIX = "pbkdf2_sha256"

MAX_USERNAME_CHARS = 120
MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 1024


def pbkdf2_iterations() -> int:
    """Work factor, overridable for tests via PS_AUTH_PBKDF2_ITERATIONS."""
    override = os.environ.get("PS_AUTH_PBKDF2_ITERATIONS")
    if override:
        try:
            value = int(override)
        except ValueError:
            return DEFAULT_PBKDF2_ITERATIONS
        return value if value > 0 else DEFAULT_PBKDF2_ITERATIONS
    return DEFAULT_PBKDF2_ITERATIONS


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def validate_username(username: str) -> str:
    """Return a cleaned username or raise ValueError. Never logs the value."""
    if not isinstance(username, str):
        raise ValueError("username must be a string")
    cleaned = username.strip()
    if not (1 <= len(cleaned) <= MAX_USERNAME_CHARS):
        raise ValueError("username is empty or too long")
    return cleaned


def validate_password(password: str) -> str:
    """Return the password or raise ValueError. The value is never logged."""
    if not isinstance(password, str):
        raise ValueError("password must be a string")
    if not (MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS):
        raise ValueError("password does not meet length requirements")
    return password


def hash_password(password: str, *, iterations: int | None = None) -> str:
    """Hash a password with a fresh random salt. Returns the encoded string."""
    validate_password(password)
    work_factor = iterations if iterations is not None else pbkdf2_iterations()
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, password.encode("utf-8"), salt, work_factor, HASH_BYTES
    )
    return f"{HASH_PREFIX}${work_factor}${salt.hex()}${derived.hex()}"


def parse_password_hash(encoded: str) -> tuple[int, bytes, bytes] | None:
    """Parse a stored PBKDF2 string into (iterations, salt, expected).

    Returns None for anything that is not a well-formed encoded hash. Never
    logs the input, which is a credential-equivalent value.
    """
    if not isinstance(encoded, str):
        return None
    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != HASH_PREFIX:
        return None
    _, iterations_str, salt_hex, hash_hex = parts
    try:
        iterations = int(iterations_str)
    except ValueError:
        return None
    if iterations <= 0:
        return None
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return None
    if not salt or not expected:
        return None
    return iterations, salt, expected


def validate_password_hash(encoded: str) -> str:
    """Return the encoded hash or raise ValueError. Never logs the value."""
    if parse_password_hash(encoded) is None:
        raise ValueError("password hash is not a valid PBKDF2 encoded hash")
    return encoded


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against a stored hash. False on any malformed input.

    Never raises and never logs; a corrupt or unknown-format stored hash simply
    fails verification (fail closed).
    """
    if not isinstance(password, str):
        return False
    parsed = parse_password_hash(encoded)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    derived = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations, len(expected)
    )
    return hmac.compare_digest(derived, expected)


# --- Users ------------------------------------------------------------------


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    role: str = "user",
    is_active: bool = True,
) -> sqlite3.Row:
    """Insert a new user with a hashed password. Raises ValueError on input.

    Raises sqlite3.IntegrityError if the username already exists — callers
    (e.g. bootstrap) must handle this explicitly and never overwrite.
    """
    cleaned = validate_username(username)
    encoded = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, is_active, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (cleaned, encoded, role, 1 if is_active else 0, _iso(_utc_now())),
    )
    return get_user_by_id(conn, cur.lastrowid)


def create_user_with_hash(
    conn: sqlite3.Connection,
    *,
    username: str,
    password_hash: str,
    role: str = "user",
    is_active: bool = True,
) -> sqlite3.Row:
    """Insert a new user from a pre-computed, validated PBKDF2 hash.

    Used by environment-driven bootstrap so the plaintext password does not have
    to be configured. The hash must already be in the exact format produced by
    ``hash_password``; otherwise ValueError is raised. Never logs the hash.
    """
    cleaned = validate_username(username)
    validate_password_hash(password_hash)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, is_active, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (cleaned, password_hash, role, 1 if is_active else 0, _iso(_utc_now())),
    )
    return get_user_by_id(conn, cur.lastrowid)


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"user {user_id} not found")
    return row


def get_user_by_username(
    conn: sqlite3.Connection, username: str
) -> sqlite3.Row | None:
    try:
        cleaned = validate_username(username)
    except ValueError:
        return None
    return conn.execute(
        "SELECT * FROM users WHERE username = ?", (cleaned,)
    ).fetchone()


def count_users(conn: sqlite3.Connection, *, active_only: bool = False) -> int:
    """Number of user rows, optionally only those currently active.

    Read-only: never creates, deletes, activates or deactivates anything.
    """
    if active_only:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_active = 1"
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"])


class SingleUserPolicyError(RuntimeError):
    """Raised when the database violates the strict single-user demo policy.

    The message is safe to log: it never contains usernames, passwords, hashes
    or tokens.
    """


def verify_single_user_policy(conn: sqlite3.Connection) -> None:
    """Fail closed unless the database has at most one ACTIVE user.

    Read-only. The single-user local demo permits zero accounts (before initial
    setup) or exactly one active account. If more than one active account
    exists the application must not start, because it would silently operate as
    a multi-user system with no authorization boundary.

    This is a startup boundary check only: it does not prevent arbitrary direct
    database edits while the process is running, and it never deletes,
    deactivates or otherwise alters accounts.
    """
    active = count_users(conn, active_only=True)
    if active > 1:
        raise SingleUserPolicyError(
            "This demo enforces a single-user policy but the database has "
            f"{active} active accounts. Refusing to start. Resolve this by "
            "inspecting the 'users' table and keeping at most one active "
            "account (this is a manual step; nothing was changed)."
        )


# --- Sessions ---------------------------------------------------------------

DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60  # 12 hours


def session_ttl_seconds() -> int:
    """Session lifetime, overridable for tests via PS_AUTH_SESSION_TTL."""
    override = os.environ.get("PS_AUTH_SESSION_TTL")
    if override:
        try:
            value = int(override)
        except ValueError:
            return DEFAULT_SESSION_TTL_SECONDS
        return value if value > 0 else DEFAULT_SESSION_TTL_SECONDS
    return DEFAULT_SESSION_TTL_SECONDS


def hash_token(token: str) -> str:
    """SHA-256 hex of a session token. Only this is ever persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    """Return a fresh random CSRF token (returned once to the caller)."""
    return secrets.token_urlsafe(32)


@dataclass
class SessionContext:
    """Verified caller identity derived from a server-side session."""

    session_id: int
    user_id: int
    username: str
    role: str
    expires_at: str


@dataclass
class IssuedSession:
    """A newly created session: raw tokens returned exactly once."""

    token: str
    csrf_token: str


def create_session(
    conn: sqlite3.Connection, user_id: int, *, ttl_seconds: int | None = None
) -> IssuedSession:
    """Create a session for an active user.

    Returns the RAW session token and a RAW CSRF token exactly once. Only the
    SHA-256 hashes of both are stored. Raises LookupError if the user does not
    exist and PermissionError if the user is inactive (fail closed). The caller
    is responsible for committing the ambient transaction.
    """
    user = get_user_by_id(conn, user_id)  # raises LookupError
    if not user["is_active"]:
        raise PermissionError("user is inactive")

    ttl = ttl_seconds if ttl_seconds is not None else session_ttl_seconds()
    now = _utc_now()
    token = secrets.token_urlsafe(32)
    csrf_token = generate_csrf_token()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at,"
        " revoked_at, csrf_token_hash) VALUES (?, ?, ?, ?, NULL, ?)",
        (
            hash_token(token),
            user_id,
            _iso(now),
            _iso(now + timedelta(seconds=ttl)),
            hash_token(csrf_token),
        ),
    )
    return IssuedSession(token=token, csrf_token=csrf_token)


def resolve_session(conn: sqlite3.Connection, token: str) -> SessionContext | None:
    """Return the verified identity for a token, or None (fail closed).

    Returns None for missing/blank tokens, unknown tokens, revoked sessions,
    expired sessions, inactive users and users that no longer exist. Never
    raises for bad input and never logs the token.
    """
    if not isinstance(token, str) or not token:
        return None
    row = conn.execute(
        "SELECT s.id AS session_id, s.user_id, s.expires_at, s.revoked_at,"
        " u.username, u.role, u.is_active"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if row is None or row["revoked_at"] is not None:
        return None
    if not row["is_active"]:
        return None
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _utc_now():
        return None
    return SessionContext(
        session_id=row["session_id"],
        user_id=row["user_id"],
        username=row["username"],
        role=row["role"],
        expires_at=row["expires_at"],
    )


def verify_csrf(conn: sqlite3.Connection, session_id: int, presented: str) -> bool:
    """Constant-time check of a presented CSRF token against the session.

    False for missing/blank input, unknown sessions and mismatches (fail
    closed). Never logs the token.
    """
    if not isinstance(presented, str) or not presented:
        return False
    row = conn.execute(
        "SELECT csrf_token_hash FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None or not row["csrf_token_hash"]:
        return False
    return hmac.compare_digest(hash_token(presented), row["csrf_token_hash"])


def revoke_session(conn: sqlite3.Connection, token: str) -> bool:
    """Revoke a session by token. True if a live session was revoked."""
    if not isinstance(token, str) or not token:
        return False
    cur = conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (_iso(_utc_now()), hash_token(token)),
    )
    return cur.rowcount == 1


def delete_expired_sessions(conn: sqlite3.Connection) -> int:
    """Delete expired or already-revoked session rows. Returns rows removed."""
    cur = conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
        (_iso(_utc_now()),),
    )
    return cur.rowcount
