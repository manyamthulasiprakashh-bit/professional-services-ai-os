"""One-time user bootstrap / rotation CLI.

There are two supported ways to create the first login user:

1. This interactive CLI (local development, or a host with a shell).
2. Environment-driven startup bootstrap for hosts without a shell (see
   ``PS_BOOTSTRAP_*`` in ``app.bootstrap`` and the README deployment section).

Both share the same rules in ``app.bootstrap``: no hardcoded default username
or password, no committed credentials, users are never created automatically
without configuration, an existing user is never overwritten, and at most one
account is permitted.

Run from the project root:

    backend/.venv/bin/python -m scripts.create_user

The username is prompted (or passed with ``--username``; usernames are not
secret). The password is read with a hidden prompt so it does not appear in the
process listing or shell history — do NOT pass the password as a command-line
argument.

To produce the value for ``PS_BOOTSTRAP_PASSWORD_HASH`` without storing the
plaintext password, use ``--print-hash``: it reads a hidden password and prints
only the encoded PBKDF2 hash.

An existing user is never overwritten: re-running for a taken username fails
unless ``--rotate`` is given deliberately, which replaces only that user's
password hash.
"""

import argparse
import getpass
import sys

from app import auth
from app.bootstrap import BootstrapError, bootstrap_user
from app.db import db_session

__all__ = ["BootstrapError", "bootstrap_user", "main"]


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise BootstrapError("Passwords did not match.")
    return password


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
    parser.add_argument(
        "--print-hash",
        dest="print_hash",
        action="store_true",
        help=(
            "Read a password from a hidden prompt and print only its PBKDF2 "
            "hash (for PS_BOOTSTRAP_PASSWORD_HASH), then exit. Does not touch "
            "the database."
        ),
    )
    args = parser.parse_args(argv)

    if args.print_hash:
        try:
            password = _prompt_password()
        except BootstrapError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nAborted.", file=sys.stderr)
            return 1
        print(auth.hash_password(password))
        return 0

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
