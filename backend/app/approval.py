"""Human approval and controlled execution of persisted proposals (Step 5.5).

This is the ONLY place a proposal can cause a write. It supports exactly one
action (`create_task`) against an existing matter, loaded from the persisted
proposal payload — never from a client-submitted body.

Execution runs inside a single SQLite transaction begun with `BEGIN IMMEDIATE`
so concurrent approvers serialize and only one wins; the guarded status UPDATE
makes duplicate/retried approvals inert. On any failure the task insert and all
execution-state changes are rolled back, and a safe `execution_failed` audit
event is recorded only AFTER the rollback is confirmed.
"""

import hashlib
import json
import re
import sqlite3

from . import repository
from .schemas import ProposedTask, Task, TaskCreate

ACTION_CREATE_TASK = "create_task"
ALLOWED_ACTION_TYPES = frozenset({ACTION_CREATE_TASK})

MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 1000

APPROVAL_NOTICE = (
    "Approved by a human. The task below was created exactly once. This is a "
    "local demo: approval is not identity-based and carries no authorisation."
)
REJECTION_NOTICE = "Rejected by a human. No task was created."
FAILED_NOTICE = "Execution failed; no task was created. The proposal is recorded as failed."

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def canonical_payload(payload: ProposedTask) -> str:
    """Canonical JSON used for both storage and hashing (stable ordering)."""
    return json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":"))


def payload_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProposalConflict(Exception):
    """The proposal is not in a decidable state (already decided/executed)."""


class ProposalBusy(Exception):
    """Retryable infrastructure failure: the database is locked by a writer."""


class ProposalInvalid(Exception):
    """Stored payload is tampered, malformed, or fails revalidation."""


def _validate_task_fields(payload: ProposedTask) -> None:
    """Revalidate stored fields against current server rules. No rewriting."""
    if not isinstance(payload.title, str):
        raise ProposalInvalid("invalid title")
    title = payload.title.strip()
    if not (1 <= len(title) <= MAX_TITLE_CHARS):
        raise ProposalInvalid("invalid title length")
    if not isinstance(payload.description, str):
        raise ProposalInvalid("invalid description")
    if not (1 <= len(payload.description.strip()) <= MAX_DESCRIPTION_CHARS):
        raise ProposalInvalid("invalid description length")
    if isinstance(payload.matter_id, bool) or not isinstance(payload.matter_id, int):
        raise ProposalInvalid("invalid matter id")
    if payload.matter_id < 1:
        raise ProposalInvalid("invalid matter id")
    if payload.due_date is not None:
        if not isinstance(payload.due_date, str) or not _DATE_RE.match(payload.due_date):
            raise ProposalInvalid("invalid due date")


def _load_validated_proposal(conn: sqlite3.Connection, proposal_id: str) -> tuple[sqlite3.Row, ProposedTask]:
    """Load a proposal and confirm its payload matches its stored hash."""
    row = repository.get_proposal(conn, proposal_id)  # raises LookupError
    try:
        raw = json.loads(row["payload_json"])
        payload = ProposedTask.model_validate(raw)
    except Exception as exc:  # corrupt/unknown-shape payload
        raise ProposalInvalid("stored payload is unreadable") from exc
    if canonical_payload(payload) != row["payload_json"]:
        raise ProposalInvalid("stored payload is not canonical")
    if payload_hash(canonical_payload(payload)) != row["payload_hash"]:
        raise ProposalInvalid("payload hash mismatch")
    if row["action_type"] not in ALLOWED_ACTION_TYPES:
        raise ProposalInvalid("action type not allowed")
    _validate_task_fields(payload)
    return row, payload


def execute_proposal(
    conn: sqlite3.Connection, proposal_id: str, actor_user_id: int | None = None
) -> tuple[str, Task | None, str | None]:
    """Execute an approved create_task proposal exactly once.

    `actor_user_id` is the authenticated user who approved; it is persisted as
    the proposal's `decided_by_user_id` and on the `executed` audit event within
    the same transaction, and is never taken from a client body. Returns
    `(status, created_task, execution_error)`. Raises LookupError for an unknown
    proposal and ProposalConflict when the proposal is not pending.
    """
    # Validate before taking the write lock (still inside the request txn).
    row, payload = _load_validated_proposal(conn, proposal_id)

    if row["status"] != "pending_approval":
        raise ProposalConflict(row["status"])

    # Matter must still exist at execution time.
    try:
        repository.get_matter(conn, payload.matter_id)
    except LookupError as exc:
        raise ProposalInvalid("matter not found") from exc

    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        # Database is locked by another writer; bounded wait already applied.
        raise ProposalBusy("database is busy") from exc

    created_task_id: int | None = None
    try:
        if not repository.claim_pending_proposal(conn, proposal_id, actor_user_id):
            raise ProposalConflict("already decided")

        task = repository.create_task(
            conn,
            TaskCreate(
                matter_id=payload.matter_id,
                title=payload.title.strip(),
                status="todo",
                priority="medium",
            ),
        )
        created_task_id = task["id"]
        repository.mark_executed(conn, proposal_id, created_task_id, actor_user_id)
        conn.commit()
    except ProposalConflict:
        conn.rollback()
        raise
    except sqlite3.OperationalError as exc:
        # Retryable infrastructure failure (e.g. lock): leave the proposal
        # pending and report a conflict/busy, do NOT mark it failed.
        conn.rollback()
        raise ProposalBusy("database is busy") from exc
    except Exception as exc:
        # Roll back task insert and all execution-state changes together.
        conn.rollback()
        _record_failure(conn, proposal_id, exc, actor_user_id)
        return "failed", None, _safe_error(exc)

    created = repository.get_task(conn, created_task_id)
    return "executed", Task.model_validate(repository.row_to_dict(created)), None


def reject_proposal(
    conn: sqlite3.Connection, proposal_id: str, actor_user_id: int | None = None
) -> str:
    """Reject a pending proposal atomically. Returns the new status.

    `actor_user_id` is the authenticated user who rejected; it is persisted as
    the proposal's `decided_by_user_id` and on the `rejected` audit event in the
    same transaction.
    """
    repository.get_proposal(conn, proposal_id)  # raises LookupError
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        raise ProposalBusy("database is busy") from exc
    try:
        if not repository.mark_rejected(conn, proposal_id, actor_user_id):
            conn.rollback()
            raise ProposalConflict("already decided")
        conn.commit()
    except ProposalConflict:
        raise
    except Exception:
        conn.rollback()
        raise
    return "rejected"


def _record_failure(
    conn: sqlite3.Connection,
    proposal_id: str,
    exc: Exception,
    actor_user_id: int | None = None,
) -> None:
    """Record a safe failure in a fresh transaction, after rollback succeeded."""
    try:
        repository.mark_failed(conn, proposal_id, _safe_error(exc), actor_user_id)
        conn.commit()
    except Exception:
        conn.rollback()


def _safe_error(exc: Exception) -> str:
    """Map an exception to a safe, non-sensitive category string."""
    if isinstance(exc, ProposalInvalid):
        return "invalid_or_tampered_proposal"
    if isinstance(exc, sqlite3.OperationalError):
        return "database_error"
    if isinstance(exc, sqlite3.IntegrityError):
        return "reference_integrity_error"
    return "unexpected_execution_error"
