"""Data access functions. Pure sqlite3 — no FastAPI imports here."""

import sqlite3
from datetime import datetime, timezone

from .schemas import ClientCreate, MatterCreate, TaskCreate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert an sqlite3.Row to a plain dict (for Pydantic validation)."""
    return {key: row[key] for key in row.keys()}


# --- Clients ---------------------------------------------------------------


def create_client(conn: sqlite3.Connection, payload: ClientCreate) -> sqlite3.Row:
    cur = conn.execute(
        "INSERT INTO clients (name, email, created_at) VALUES (?, ?, ?)",
        (payload.name, payload.email, _utc_now()),
    )
    return get_client(conn, cur.lastrowid)


def get_client(conn: sqlite3.Connection, client_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise LookupError(f"client {client_id} not found")
    return row


def list_clients(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM clients ORDER BY id").fetchall()


# --- Matters ----------------------------------------------------------------


def create_matter(conn: sqlite3.Connection, payload: MatterCreate) -> sqlite3.Row:
    cur = conn.execute(
        "INSERT INTO matters (client_id, title, status, created_at)"
        " VALUES (?, ?, ?, ?)",
        (payload.client_id, payload.title, payload.status, _utc_now()),
    )
    return get_matter(conn, cur.lastrowid)


def get_matter(conn: sqlite3.Connection, matter_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM matters WHERE id = ?", (matter_id,)).fetchone()
    if row is None:
        raise LookupError(f"matter {matter_id} not found")
    return row


def list_matters(
    conn: sqlite3.Connection, client_id: int | None = None
) -> list[sqlite3.Row]:
    if client_id is None:
        return conn.execute("SELECT * FROM matters ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM matters WHERE client_id = ? ORDER BY id", (client_id,)
    ).fetchall()


# --- Tasks ------------------------------------------------------------------


def create_task(conn: sqlite3.Connection, payload: TaskCreate) -> sqlite3.Row:
    cur = conn.execute(
        "INSERT INTO tasks (matter_id, title, status, priority, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            payload.matter_id,
            payload.title,
            payload.status,
            payload.priority,
            _utc_now(),
        ),
    )
    return get_task(conn, cur.lastrowid)


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise LookupError(f"task {task_id} not found")
    return row


def list_tasks(
    conn: sqlite3.Connection,
    matter_id: int | None = None,
    status: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list[object] = []
    if matter_id is not None:
        query += " AND matter_id = ?"
        params.append(matter_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id"
    return conn.execute(query, params).fetchall()


# --- Documents --------------------------------------------------------------


def create_document(
    conn: sqlite3.Connection,
    *,
    matter_id: int,
    original_filename: str,
    stored_name: str,
    size_bytes: int,
    sha256: str,
    status: str = "uploaded",
    extraction_error: str | None = None,
    chunks: list | None = None,
) -> sqlite3.Row:
    """Insert a document and its extracted chunks in one transaction."""
    cur = conn.execute(
        "INSERT INTO documents (matter_id, original_filename, stored_name,"
        " size_bytes, sha256, status, extraction_error, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            matter_id,
            original_filename,
            stored_name,
            size_bytes,
            sha256,
            status,
            extraction_error,
            _utc_now(),
        ),
    )
    document_id = cur.lastrowid
    for chunk in chunks or []:
        conn.execute(
            "INSERT INTO document_chunks (document_id, page_number, content)"
            " VALUES (?, ?, ?)",
            (document_id, chunk.page_number, chunk.content),
        )
    return get_document(conn, document_id)


def get_document(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"document {document_id} not found")
    return row


def list_documents(
    conn: sqlite3.Connection, matter_id: int | None = None
) -> list[sqlite3.Row]:
    if matter_id is None:
        return conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM documents WHERE matter_id = ? ORDER BY id", (matter_id,)
    ).fetchall()


def find_document_by_sha256(
    conn: sqlite3.Connection, matter_id: int, sha256: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM documents WHERE matter_id = ? AND sha256 = ?",
        (matter_id, sha256),
    ).fetchone()


def replace_document_chunks(
    conn: sqlite3.Connection, document_id: int, chunks: list
) -> None:
    """Replace a document's chunks. Chunk ids change, so any existing
    embeddings for the document are invalidated (re-index required)."""
    conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM chunk_embeddings WHERE document_id = ?", (document_id,))
    for chunk in chunks:
        conn.execute(
            "INSERT INTO document_chunks (document_id, page_number, content)"
            " VALUES (?, ?, ?)",
            (document_id, chunk.page_number, chunk.content),
        )


def set_document_extraction_status(
    conn: sqlite3.Connection,
    document_id: int,
    status: str,
    extraction_error: str | None = None,
) -> sqlite3.Row:
    conn.execute(
        "UPDATE documents SET status = ?, extraction_error = ? WHERE id = ?",
        (status, extraction_error, document_id),
    )
    return get_document(conn, document_id)


def get_document_chunks(
    conn: sqlite3.Connection, document_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM document_chunks WHERE document_id = ? ORDER BY id",
        (document_id,),
    ).fetchall()


# --- Action proposals + audit (Step 5.5) ------------------------------------


def create_proposal(
    conn: sqlite3.Connection,
    *,
    proposal_id: str,
    action_type: str,
    payload_json: str,
    payload_hash: str,
    evidence_json: str,
    model: str,
) -> sqlite3.Row:
    """Insert a pending proposal and its `proposed` audit event.

    Both rows are written in the caller's transaction so they commit together.
    """
    now = _utc_now()
    conn.execute(
        "INSERT INTO action_proposals (proposal_id, action_type, status,"
        " payload_json, payload_hash, evidence_json, model, created_at, updated_at)"
        " VALUES (?, ?, 'pending_approval', ?, ?, ?, ?, ?, ?)",
        (
            proposal_id,
            action_type,
            payload_json,
            payload_hash,
            evidence_json,
            model,
            now,
            now,
        ),
    )
    append_audit(conn, proposal_id, "proposed")
    return get_proposal(conn, proposal_id)


def get_proposal(conn: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM action_proposals WHERE proposal_id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"proposal {proposal_id} not found")
    return row


def list_audit(conn: sqlite3.Connection, proposal_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM action_audit WHERE proposal_id = ? ORDER BY id",
        (proposal_id,),
    ).fetchall()


def append_audit(
    conn: sqlite3.Connection,
    proposal_id: str,
    event: str,
    detail: str | None = None,
    actor_user_id: int | None = None,
) -> None:
    """Append one audit row in the caller's transaction. Safe metadata only.

    `actor_user_id` is the authenticated user who caused the event, or None for
    events recorded without an accountable actor (e.g. proposal creation, or
    legacy rows). The audit trail is append-only.
    """
    conn.execute(
        "INSERT INTO action_audit (proposal_id, event, detail, actor_user_id,"
        " created_at) VALUES (?, ?, ?, ?, ?)",
        (proposal_id, event, detail, actor_user_id, _utc_now()),
    )


def claim_pending_proposal(
    conn: sqlite3.Connection, proposal_id: str, actor_user_id: int | None = None
) -> bool:
    """Atomically move a pending proposal to `approved`. True if it was pending.

    The guarded UPDATE only matches `pending_approval`, so a second/concurrent
    caller gets False without side effects. `actor_user_id` records who decided.
    """
    now = _utc_now()
    cur = conn.execute(
        "UPDATE action_proposals SET status = 'approved', decided_at = ?,"
        " decided_by_user_id = ?, updated_at = ? WHERE proposal_id = ?"
        " AND status = 'pending_approval'",
        (now, actor_user_id, now, proposal_id),
    )
    return cur.rowcount == 1


def mark_executed(
    conn: sqlite3.Connection,
    proposal_id: str,
    task_id: int,
    actor_user_id: int | None = None,
) -> None:
    now = _utc_now()
    conn.execute(
        "UPDATE action_proposals SET status = 'executed', created_task_id = ?,"
        " executed_at = ?, updated_at = ? WHERE proposal_id = ?",
        (task_id, now, now, proposal_id),
    )
    append_audit(
        conn, proposal_id, "executed", f"task_id={task_id}", actor_user_id
    )


def mark_rejected(
    conn: sqlite3.Connection, proposal_id: str, actor_user_id: int | None = None
) -> bool:
    """Transition only a pending proposal to `rejected`. True if it was pending."""
    now = _utc_now()
    cur = conn.execute(
        "UPDATE action_proposals SET status = 'rejected', decided_at = ?,"
        " decided_by_user_id = ?, updated_at = ? WHERE proposal_id = ?"
        " AND status = 'pending_approval'",
        (now, actor_user_id, now, proposal_id),
    )
    if cur.rowcount != 1:
        return False
    append_audit(conn, proposal_id, "rejected", actor_user_id=actor_user_id)
    return True


def mark_failed(
    conn: sqlite3.Connection,
    proposal_id: str,
    error: str,
    actor_user_id: int | None = None,
) -> None:
    """Record a safe execution failure after any task rollback is confirmed."""
    now = _utc_now()
    conn.execute(
        "UPDATE action_proposals SET status = 'failed', execution_error = ?,"
        " updated_at = ? WHERE proposal_id = ? AND status IN"
        " ('pending_approval','approved')",
        (error, now, proposal_id),
    )
    append_audit(conn, proposal_id, "execution_failed", error, actor_user_id)
