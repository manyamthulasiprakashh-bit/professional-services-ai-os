"""One-time user bootstrap / rotation CLI (Step 6.2C-1).

Creates the first login user with an operator-supplied username and an
interactively entered password. There is NO hardcoded default username or
password, and no committed credentials. The script never prints the password
or its hash, and it never creates a user automatically at application startup.

Run from the project root:

    backend/.venv/bin/python -m scripts.create_user

The username is prompted (or passed with ``--username``; usernames are not
secret). The password is read with a hidden prompt so it does not appear in the
process listing or shell history — do NOT pass the password as a command-line
argument.

An existing user is never overwritten: re-running for a taken username fails
unless ``--rotate`` is given deliberately, which replaces only that user's
password hash.
"""

import argparse
import getpass
import sys

from app import auth
from app.db import db_session


class BootstrapError(Exception):
    """A safe, user-facing bootstrap failure (never contains secrets)."""


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise BootstrapError("Passwords did not match.")
    return password


def bootstrap_user(
    conn,
    *,
    username: str,
    password: str,
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

    Raises BootstrapError for validation problems, an already-taken username
    without ``rotate``, or an attempt to add a second account. Never logs or
    returns the password, and never surfaces secrets in messages.
    """
    try:
        cleaned = auth.validate_username(username)
        auth.validate_password(password)
    except ValueError as exc:
        raise BootstrapError(str(exc)) from exc

    existing = auth.get_user_by_username(conn, cleaned)
    if existing is not None:
        # Same account. Overwrite only when explicitly rotating.
        if not rotate:
            raise BootstrapError(
                f"User '{cleaned}' already exists. Refusing to overwrite; "
                "use --rotate to deliberately replace its password."
            )
        encoded = auth.hash_password(password)
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

    auth.create_user(conn, username=cleaned, password=password, role=role)
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the initial login user (no default credentials)."
    )
    parser.add_argument(
        "--username",
        help="Username to create. If omitted, you are prompted for it.",
    )
    parser.add_argument(
        "--role",
        choices=("user", "approver"),
        default="user",
        help=(
            "Reserved label stored on the account (default: user). This demo "
            "enforces NO role-based authorization, so 'approver' grants NO "
            "privileged access."
        ),
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Deliberately replace the password of an existing user.",
    )
    args = parser.parse_args(argv)

    username = args.username or input("Username: ").strip()
    try:
        password = _prompt_password()
        with db_session() as conn:
            stored = bootstrap_user(
                conn,
                username=username,
                password=password,
                role=args.role,
                rotate=args.rotate,
            )
    except BootstrapError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 1

    action = "Password rotated for" if args.rotate else "Created user"
    print(f"{action} '{stored}'. No password was displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
