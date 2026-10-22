"""SQLite connection management and schema for the business operations API.

The database lives under backend/data/ by default. The path can be overridden
with the PS_APP_DB environment variable (used by tests to isolate data).
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

# Bounded lock wait so concurrent writers fail with a clear error instead of
# hanging. Overridable for tests.
DEFAULT_BUSY_TIMEOUT_SECONDS = 5.0


def busy_timeout_seconds() -> float:
    override = os.environ.get("PS_SQLITE_BUSY_TIMEOUT")
    return float(override) if override else DEFAULT_BUSY_TIMEOUT_SECONDS

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    email      TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS matters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(id),
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'open'
               CHECK (status IN ('open', 'closed')),
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id  INTEGER NOT NULL REFERENCES matters(id),
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo', 'in_progress', 'done')),
    priority   TEXT    NOT NULL DEFAULT 'medium'
               CHECK (priority IN ('low', 'medium', 'high')),
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matters_client_id ON matters(client_id);
CREATE INDEX IF NOT EXISTS idx_tasks_matter_id ON tasks(matter_id);

CREATE TABLE IF NOT EXISTS documents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id          INTEGER NOT NULL REFERENCES matters(id),
    original_filename  TEXT    NOT NULL,
    stored_name        TEXT    NOT NULL UNIQUE,
    size_bytes         INTEGER NOT NULL,
    sha256             TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'uploaded'
                       CHECK (status IN ('uploaded', 'extracted', 'extraction_failed')),
    extraction_error   TEXT,
    created_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    page_number INTEGER,
    content     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_matter_id ON documents(matter_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id);

-- Step 5.3: chunk embeddings for semantic retrieval. Vectors are float32
-- BLOBs (array('f') bytes); embed_model + dimension record provenance so a
-- model change forces re-indexing instead of mixed-model ranking.
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id    INTEGER PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    embedding   BLOB    NOT NULL,
    dimension   INTEGER NOT NULL,
    embed_model TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_document_id
    ON chunk_embeddings(document_id);

-- Step 5.5: persisted, human-approved workflow proposals. Payloads are stored
-- canonically with a SHA-256 hash for tamper detection; evidence is bounded
-- references only (never full documents). `created_task_id` links the one task
-- an APPROVED create_task proposal produced, set atomically with execution.
CREATE TABLE IF NOT EXISTS action_proposals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id     TEXT    NOT NULL UNIQUE,
    action_type     TEXT    NOT NULL
                    CHECK (action_type IN ('create_task')),
    status          TEXT    NOT NULL DEFAULT 'pending_approval'
                    CHECK (status IN
                      ('pending_approval','approved','executed','rejected','failed')),
    payload_json    TEXT    NOT NULL,
    payload_hash    TEXT    NOT NULL,
    evidence_json   TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    created_task_id INTEGER REFERENCES tasks(id),
    execution_error TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    decided_at      TEXT,
    executed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_proposals_status
    ON action_proposals(status);

-- Step 5.5: append-only audit trail. Safe metadata only — no prompts, no
-- document contents, no full payloads.
CREATE TABLE IF NOT EXISTS action_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT    NOT NULL,
    event       TEXT    NOT NULL
                CHECK (event IN
                  ('proposed','approved','executed','rejected','execution_failed')),
    detail      TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_audit_proposal_id
    ON action_audit(proposal_id);

-- Step 6.2C-1: authentication foundation. `users` holds login identities;
-- `password_hash` is a self-describing PBKDF2 string (algorithm + iteration
-- count + salt + derived key), never a plaintext password. `role` is reserved
-- for later authorization; this substep does not enforce route permissions.
-- `is_active` lets an account be disabled without deleting audit history.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user'
                  CHECK (role IN ('user', 'approver')),
    is_active     INTEGER NOT NULL DEFAULT 1
                  CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL
);

-- Step 6.2C-1: opaque server-side sessions. Only the SHA-256 hash of the
-- session token is stored (`token_hash`); the raw token is returned once to
-- the caller and never persisted. Expiry and revocation are enforced
-- server-side.
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT    NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    revoked_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
"""

# Step 6.2C-2 additive columns introduced after the initial auth release, plus
# Step 6.2C-3 approval accountability actor columns. SQLite has no
# `ADD COLUMN IF NOT EXISTS`, so each entry is applied only when the column is
# actually missing. Column additions are additive and nullable, so existing rows
# and tables are never altered destructively. Historical rows (including
# pre-authentication decisions) keep NULL actors — no actor is ever fabricated.
ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, column definition)
    ("sessions", "csrf_token_hash", "TEXT"),
    # Step 6.2C-3: who (authenticated user) decided a proposal. NULL for
    # decisions made before accountability existed.
    ("action_proposals", "decided_by_user_id", "INTEGER"),
    # Step 6.2C-3: who (authenticated user) caused an audit event. NULL for
    # events recorded before accountability existed.
    ("action_audit", "actor_user_id", "INTEGER"),
]


def resolve_db_path() -> Path:
    """Return the configured database path (env override or default)."""
    override = os.environ.get("PS_APP_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema idempotently (repeatable migration).

    Runs all `CREATE ... IF NOT EXISTS` statements, then applies any additive
    column migrations that are still missing. Running this on a fresh database
    and on an existing database (including one created before the
    authentication tables/columns existed) is safe: existing tables and rows
    are never dropped, reset or altered. This is the repository's migration
    mechanism.
    """
    conn.executescript(SCHEMA_SQL)
    for table, column, definition in ADDITIVE_COLUMNS:
        if not _table_has_column(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with sane defaults and apply the schema.

    ``check_same_thread=False`` is required by the FastAPI lifecycle: synchronous
    dependencies and synchronous endpoints both run on Starlette's worker
    thread-pool, and each step may land on a different worker. A connection is
    created per request (``db_session``/``get_db``), used only by that request,
    and closed when it finishes; it is never shared between concurrent requests.
    The handoff between workers is therefore sequential, and CPython's sqlite3
    is compiled in serialized mode here (``sqlite3.threadsafety == 3``), so the
    connection itself remains safe. Transaction commit/rollback still happen
    exactly once, in ``db_session``, after the request's DB work is complete.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=busy_timeout_seconds(),
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_seconds() * 1000)}")
    apply_schema(conn)
    return conn


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    """Yield a connection and commit or roll back the transaction."""
    conn = connect(resolve_db_path())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency wrapping db_session."""
    with db_session() as conn:
        yield conn
