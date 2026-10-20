"""Programmatic launcher for the backend.

Pins the bind host to loopback (``127.0.0.1``) by default so the
unauthenticated prototype can never become LAN-accessible by accident.
Non-loopback exposure is unsafe without authentication, authorization and
deployment controls, so it is deliberately not configurable here.

Run from the project root:

    backend/.venv/bin/python -m app.launcher

The equivalent, explicitly loopback-bound uvicorn command is documented in
README.md.
"""

import uvicorn

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def resolve_bind(host: str | None = None, port: int | None = None) -> tuple[str, int]:
    """Return the (host, port) the server binds to.

    The host is always loopback. A caller may not override it, which keeps a
    single enforced default instead of a configurable external-binding switch.
    """
    return LOOPBACK_HOST, port if port is not None else DEFAULT_PORT


def run() -> None:
    """Start uvicorn bound to loopback on the default port."""
    host, port = resolve_bind()
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    run()
