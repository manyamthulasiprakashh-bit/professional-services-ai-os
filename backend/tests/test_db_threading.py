"""SQLite connection lifecycle across FastAPI worker threads.

FastAPI runs a synchronous dependency and the synchronous endpoint on
Starlette's shared worker thread-pool; consecutive steps for one request may be
executed by different workers. A per-request connection is therefore created,
used and closed sequentially across those workers. These tests reproduce that
handoff directly and fail with ``sqlite3.ProgrammingError`` if the connection is
still restricted to its creating thread.

The connection is never shared by two concurrent requests: ``get_db`` creates a
new one per request.
"""

import threading

import pytest

from app.db import connect, db_session

_INSERT_CLIENT = (
    "INSERT INTO clients (name, email, created_at) VALUES (?, NULL, ?)"
)


def _in_thread(fn):
    """Run ``fn`` on another thread, re-raising any failure on the caller."""
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - propagate to caller
            outcome["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "worker thread did not finish"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _count_clients(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"])


def test_connection_handoff_between_threads_is_supported(db_path) -> None:
    conn = connect(db_path)  # created on this thread
    try:
        def use() -> int:  # runs on a different thread (endpoint worker)
            conn.execute(_INSERT_CLIENT, ("Acme Legal", "2026-01-01T00:00:00+00:00"))
            conn.commit()
            return _count_clients(conn)

        assert _in_thread(use) == 1
    finally:
        conn.close()


def test_db_session_commits_write_used_on_another_thread(db_path) -> None:
    with db_session() as conn:
        _in_thread(
            lambda: conn.execute(
                _INSERT_CLIENT, ("Acme Legal", "2026-01-01T00:00:00+00:00")
            )
        )
    # db_session exits (commits) on the original thread after the handoff.

    fresh = connect(db_path)
    try:
        assert _count_clients(fresh) == 1
    finally:
        fresh.close()


def test_db_session_rolls_back_handoff_write_on_error(db_path) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with db_session() as conn:
            _in_thread(
                lambda: conn.execute(
                    _INSERT_CLIENT, ("Rolled Back", "2026-01-01T00:00:00+00:00")
                )
            )
            raise RuntimeError("boom")

    fresh = connect(db_path)
    try:
        assert _count_clients(fresh) == 0
    finally:
        fresh.close()
