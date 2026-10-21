"""Initial-user bootstrap (interactive CLI and environment-driven startup).

This module centralizes the single-user bootstrap rules so the CLI and the
environment-driven startup path cannot drift apart.

Security properties:
- A configured credential only ever creates a user when the database currently
  contains ZERO users, so a configured hash can never add a second account.
- Existing accounts are never overwritten or rotated implicitly. Rotation is
  only available deliberately through the CLI's ``--rotate`` flag.
- A pre-computed PBKDF2 hash (``PS_BOOTSTRAP_PASSWORD_HASH``) is preferred over
  a plaintext password, so the plaintext does not have to live in the hosting
  environment.
- The username, password, hash and other secrets are never logged. Error
  messages are safe to display.
"""

import logging
import os
import sqlite3

from . import auth

logger = logging.getLogger(__name__)

BOOTSTRAP_USERNAME_ENV = "PS_BOOTSTRAP_USERNAME"
BOOTSTRAP_PASSWORD_ENV = "PS_BOOTSTRAP_PASSWORD"
BOOTSTRAP_PASSWORD_HASH_ENV = "PS_BOOTSTRAP_PASSWORD_HASH"


class BootstrapError(Exception):
    """A safe, user-facing bootstrap failure (never contains secrets)."""


def bootstrap_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str | None = None,
    password_hash: str | None = None,
    role: str = "user",
    rotate: bool = False,
) -> str:
    """Create (or deliberately rotate) the single demo user.

    Strict single-user policy: this demo permits at most ONE account. Creating
    the first account works as before; rotating the password of the existing
    account works with ``rotate=True``; creating a *second distinct* account is
    refused by default. Existing databases that already contain more than one
    account are never modified or cleaned up — the caller is told to resolve it
    manually.

    Provide exactly one credential source: ``password_hash`` (preferred; must be
    a valid PBKDF2 encoded string) or ``password``. Raises BootstrapError for
    validation problems, an already-taken username without ``rotate``, or an
    attempt to add a second account. Never logs or returns the password or hash,
    and never surfaces secrets in messages.
    """
    try:
        cleaned = auth.validate_username(username)
    except ValueError as exc:
        raise BootstrapError(str(exc)) from exc

    if password_hash is not None:
        try:
            auth.validate_password_hash(password_hash)
        except ValueError as exc:
            raise BootstrapError(
                "The configured bootstrap password hash is not a valid PBKDF2 "
                "encoded hash."
            ) from exc
    elif password is not None:
        try:
            auth.validate_password(password)
        except ValueError as exc:
            raise BootstrapError(str(exc)) from exc
    else:
        raise BootstrapError("A password or password hash is required.")

    existing = auth.get_user_by_username(conn, cleaned)
    if existing is not None:
        # Same account. Overwrite only when explicitly rotating.
        if not rotate:
            raise BootstrapError(
                f"User '{cleaned}' already exists. Refusing to overwrite; "
                "use --rotate to deliberately replace its password."
            )
        encoded = (
            password_hash
            if password_hash is not None
            else auth.hash_password(password)
        )
        conn.execute(
            "UPDATE users SET password_hash = ?, role = ?, is_active = 1 WHERE id = ?",
            (encoded, role, existing["id"]),
        )
        return cleaned

    # The username is new: this would be an additional account.
    total = auth.count_users(conn)
    if total > 0:
        if total > 1:
            raise BootstrapError(
                "This database already contains multiple user accounts, which "
                "the single-user demo does not support. Refusing to add another. "
                "Resolve the extra accounts manually (inspect the 'users' table) "
                "before bootstrapping; nothing was changed."
            )
        raise BootstrapError(
            "This demo permits only one user account and one already exists. "
            "Refusing to create a second account. To replace the existing "
            "account's password, re-run with --rotate and that account's "
            "username; nothing was changed."
        )

    if password_hash is not None:
        auth.create_user_with_hash(
            conn, username=cleaned, password_hash=password_hash, role=role
        )
    else:
        auth.create_user(conn, username=cleaned, password=password, role=role)
    return cleaned


def bootstrap_from_env(conn: sqlite3.Connection) -> str | None:
    """Create the first user from ``PS_BOOTSTRAP_*`` environment variables.

    Returns the created username, or None when no bootstrap variables are
    configured or when any user already exists. Fails closed (BootstrapError)
    when the configuration is partial or invalid. ``PS_BOOTSTRAP_PASSWORD_HASH``
    is preferred; ``PS_BOOTSTRAP_PASSWORD`` is only read when it is absent.

    Never rotates or overwrites an existing account. Commits on success so the
    new row survives the caller closing the connection.
    """
    username = os.environ.get(BOOTSTRAP_USERNAME_ENV, "").strip()
    password_hash = os.environ.get(BOOTSTRAP_PASSWORD_HASH_ENV, "")
    password = os.environ.get(BOOTSTRAP_PASSWORD_ENV, "")

    if not (username or password_hash or password):
        return None

    if not username:
        raise BootstrapError(
            "PS_BOOTSTRAP_USERNAME must be set when bootstrap credentials are "
            "configured."
        )

    if not password_hash and not password:
        raise BootstrapError(
            "Set PS_BOOTSTRAP_PASSWORD_HASH (preferred) or "
            "PS_BOOTSTRAP_PASSWORD when PS_BOOTSTRAP_USERNAME is configured."
        )

    if auth.count_users(conn) > 0:
        # Never overwrite an existing account, even if bootstrap variables are
        # configured. This also keeps the single-user policy intact.
        return None

    if password_hash:
        created = bootstrap_user(
            conn, username=username, password_hash=password_hash
        )
    else:
        created = bootstrap_user(conn, username=username, password=password)

    conn.commit()
    logger.info("bootstrap: created initial user account")
    return created
