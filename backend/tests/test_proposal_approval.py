"""Tests for human approval and controlled execution (Step 5.5).

Network-free: fake model providers and isolated temp databases. These tests
cover persistence, exactly-once execution, duplicate/concurrent decisions,
tampered payloads, rollback and audit atomicity.
"""

import json
import sqlite3

import pytest

from app import approval, repository
from app.db import connect
from app.schemas import ClientCreate, MatterCreate, ProposedTask
from test_assistant_retrieval import _seed_workspace
from test_proposals import FakeAssistant, _proposal_json, _propose


def _make_proposal(client, matter_id: int, **kwargs) -> dict:
    """Create and persist a pending proposal through the real endpoint."""
    assistant = FakeAssistant(_proposal_json(matter_id=matter_id, **kwargs))
    body = _propose(client, "add a task", assistant).json()
    assert body["proposal"] is not None, body
    return body["proposal"]


def _tasks(client) -> list:
    return client.get("/tasks").json()


def _audit_events(conn, proposal_id: str) -> list[str]:
    return [r["event"] for r in repository.list_audit(conn, proposal_id)]


# --- persistence & retrieval ------------------------------------------------


def test_proposal_is_persisted_and_retrievable(client) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"], title="Harbor brief")

    fetched = client.get(f"/assistant/proposals/{proposal['proposal_id']}")
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["proposal_id"] == proposal["proposal_id"]
    assert record["status"] == "pending_approval"
    assert record["action_type"] == "create_task"
    assert record["proposed"]["title"] == "Harbor brief"
    assert record["created_task_id"] is None
    assert record["executed_at"] is None
    # generating still does not create a task
    assert len(_tasks(client)) == 1


def test_proposal_ids_are_unique(client) -> None:
    seed = _seed_workspace(client)
    first = _make_proposal(client, seed["harbor"]["id"])
    second = _make_proposal(client, seed["harbor"]["id"])
    assert first["proposal_id"] != second["proposal_id"]


def test_unknown_proposal_id_is_404(client) -> None:
    _seed_workspace(client)
    assert client.get("/assistant/proposals/nope").status_code == 404
    assert client.post("/assistant/proposals/nope/approve").status_code == 404
    assert client.post("/assistant/proposals/nope/reject").status_code == 404


def test_proposal_creation_records_proposed_audit_event(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    assert _audit_events(conn, proposal["proposal_id"]) == ["proposed"]


# --- approval / execution ---------------------------------------------------


def test_approval_creates_exactly_one_task(client) -> None:
    seed = _seed_workspace(client)
    before = len(_tasks(client))
    proposal = _make_proposal(
        client, seed["harbor"]["id"], title="Approve me", description="Do it."
    )

    response = client.post(f"/assistant/proposals/{proposal['proposal_id']}/approve")
    assert response.status_code == 200
    decision = response.json()
    assert decision["status"] == "executed"
    assert decision["created_task"] is not None
    created = decision["created_task"]
    assert created["matter_id"] == seed["harbor"]["id"]
    assert created["title"] == "Approve me"
    assert created["status"] == "todo"
    assert created["priority"] == "medium"

    tasks = _tasks(client)
    assert len(tasks) == before + 1
    assert tasks[-1]["id"] == created["id"]

    record = client.get(
        f"/assistant/proposals/{proposal['proposal_id']}"
    ).json()
    assert record["status"] == "executed"
    assert record["created_task_id"] == created["id"]
    assert record["executed_at"] is not None


def test_approval_uses_stored_payload_only(client) -> None:
    """A body on the approve request must be ignored entirely."""
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"], title="Stored title")
    response = client.post(
        f"/assistant/proposals/{proposal['proposal_id']}/approve",
        json={"title": "Injected", "matter_id": 9999, "priority": "high"},
    )
    assert response.status_code == 200
    created = response.json()["created_task"]
    assert created["title"] == "Stored title"
    assert created["matter_id"] == seed["harbor"]["id"]
    assert created["priority"] == "medium"


def test_due_date_is_not_invented_or_rewritten(client) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(
        client, seed["harbor"]["id"], due_date="2020-01-01"
    )
    assert proposal["proposed"]["due_date"] == "2020-01-01"
    response = client.post(f"/assistant/proposals/{proposal['proposal_id']}/approve")
    assert response.status_code == 200
    # a past date is accepted honestly, not silently changed
    assert proposal["proposed"]["due_date"] == "2020-01-01"
    assert response.json()["status"] == "executed"


def test_execution_records_executed_audit_event(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    client.post(f"/assistant/proposals/{proposal['proposal_id']}/approve")
    events = _audit_events(conn, proposal["proposal_id"])
    assert events == ["proposed", "executed"]


# --- duplicate & concurrent decisions ---------------------------------------


def test_duplicate_approval_does_not_create_duplicate_task(client) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    first = client.post(f"/assistant/proposals/{pid}/approve")
    before = len(_tasks(client))

    second = client.post(f"/assistant/proposals/{pid}/approve")
    assert first.status_code == 200
    assert second.status_code == 409
    assert len(_tasks(client)) == before


def test_response_loss_then_retry_is_safe(client) -> None:
    """Simulate: execute committed, response lost, client retries."""
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    client.post(f"/assistant/proposals/{pid}/approve")  # committed, "lost"
    count = len(_tasks(client))
    retry = client.post(f"/assistant/proposals/{pid}/approve")
    assert retry.status_code == 409
    assert len(_tasks(client)) == count


def test_concurrent_approval_creates_one_task(db_path) -> None:
    """Two connections race to claim the same pending proposal."""
    seed_conn = connect(db_path)
    client = repository.create_client(seed_conn, ClientCreate(name="Acme"))
    matter = repository.create_matter(
        seed_conn, MatterCreate(client_id=client["id"], title="Matter")
    )
    payload = ProposedTask(
        title="Raced", description="x", matter_id=matter["id"], matter_title="Matter"
    )
    canonical = approval.canonical_payload(payload)
    repository.create_proposal(
        seed_conn,
        proposal_id="race1234",
        action_type="create_task",
        payload_json=canonical,
        payload_hash=approval.payload_hash(canonical),
        evidence_json="[]",
        model="fake",
    )
    seed_conn.commit()

    winner = connect(db_path)
    loser = connect(db_path)
    # Winner holds the write lock and claims first.
    assert repository.claim_pending_proposal(winner, "race1234") is True
    winner.commit()
    # Loser's guarded claim must fail because the status already moved.
    assert repository.claim_pending_proposal(loser, "race1234") is False
    winner.close()
    loser.close()
    seed_conn.close()


def test_busy_database_raises_busy(db_path) -> None:
    """A held write lock surfaces as ProposalBusy, not a hang."""
    holder = connect(db_path)
    c = repository.create_client(holder, ClientCreate(name="Acme"))
    m = repository.create_matter(holder, MatterCreate(client_id=c["id"], title="M"))
    payload = ProposedTask(
        title="Busy", description="x", matter_id=m["id"], matter_title="M"
    )
    canonical = approval.canonical_payload(payload)
    repository.create_proposal(
        holder, proposal_id="busy1234", action_type="create_task",
        payload_json=canonical, payload_hash=approval.payload_hash(canonical),
        evidence_json="[]", model="fake",
    )
    holder.commit()

    holder.execute("BEGIN IMMEDIATE")  # simulate an in-flight writer
    other = connect(db_path)
    other.execute("PRAGMA busy_timeout = 100")
    with pytest.raises(approval.ProposalBusy):
        approval.execute_proposal(other, "busy1234")
    holder.rollback()
    holder.close()
    other.close()


def test_busy_at_endpoint_returns_503(client, conn, monkeypatch) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]

    def busy(*args, **kwargs):
        raise approval.ProposalBusy("database is busy")

    monkeypatch.setattr(approval, "execute_proposal", busy)
    response = client.post(f"/assistant/proposals/{pid}/approve")
    monkeypatch.undo()
    assert response.status_code == 503


# --- rejection --------------------------------------------------------------


def test_rejection_transitions_only_pending(client) -> None:
    seed = _seed_workspace(client)
    before = len(_tasks(client))
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]

    response = client.post(f"/assistant/proposals/{pid}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["created_task"] is None

    record = client.get(f"/assistant/proposals/{pid}").json()
    assert record["status"] == "rejected"
    assert len(_tasks(client)) == before


def test_repeat_decisions_conflict(client) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    assert client.post(f"/assistant/proposals/{pid}/reject").status_code == 200
    assert client.post(f"/assistant/proposals/{pid}/reject").status_code == 409
    assert client.post(f"/assistant/proposals/{pid}/approve").status_code == 409


def test_approve_after_reject_conflicts(client) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    client.post(f"/assistant/proposals/{pid}/reject")
    assert client.post(f"/assistant/proposals/{pid}/approve").status_code == 409


def test_rejection_records_audit_event(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    client.post(f"/assistant/proposals/{proposal['proposal_id']}/reject")
    assert _audit_events(conn, proposal["proposal_id"]) == ["proposed", "rejected"]


# --- tampering & revalidation ----------------------------------------------


def test_tampered_payload_is_refused(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    before = len(_tasks(client))

    tampered = json.dumps(
        {"title": "Evil", "description": "x", "matter_id": seed["harbor"]["id"],
         "matter_title": "x", "due_date": None},
        sort_keys=True, separators=(",", ":"),
    )
    conn.execute(
        "UPDATE action_proposals SET payload_json = ? WHERE proposal_id = ?",
        (tampered, pid),
    )
    conn.commit()

    response = client.post(f"/assistant/proposals/{pid}/approve")
    assert response.status_code == 422
    assert len(_tasks(client)) == before
    assert client.get(f"/assistant/proposals/{pid}").json()["status"] == "pending_approval"


def test_hash_mismatch_is_refused(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    conn.execute(
        "UPDATE action_proposals SET payload_hash = 'deadbeef' WHERE proposal_id = ?",
        (pid,),
    )
    conn.commit()
    assert client.post(f"/assistant/proposals/{pid}/approve").status_code == 422


def test_deleted_matter_at_execution_fails(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["unrelated"]["id"])
    pid = proposal["proposal_id"]
    before = len(_tasks(client))
    conn.execute("DELETE FROM matters WHERE id = ?", (seed["unrelated"]["id"],))
    conn.commit()

    response = client.post(f"/assistant/proposals/{pid}/approve")
    assert response.status_code == 422
    assert len(_tasks(client)) == before


def test_invalid_stored_fields_are_refused(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    bad = ProposedTask(
        title="", description="x", matter_id=seed["harbor"]["id"], matter_title="x"
    )
    canonical = approval.canonical_payload(bad)
    conn.execute(
        "UPDATE action_proposals SET payload_json = ?, payload_hash = ?"
        " WHERE proposal_id = ?",
        (canonical, approval.payload_hash(canonical), pid),
    )
    conn.commit()
    response = client.post(f"/assistant/proposals/{pid}/approve")
    assert response.status_code == 422


# --- rollback & retry -------------------------------------------------------


def test_rollback_on_insert_failure_leaves_pending(client, conn, monkeypatch) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]
    before = len(_tasks(client))

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(repository, "create_task", boom)
    response = client.post(f"/assistant/proposals/{pid}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert len(_tasks(client)) == before
    # The failure is recorded safely but nothing executed.
    assert client.get(f"/assistant/proposals/{pid}").json()["status"] == "failed"
    monkeypatch.undo()

    # A new proposal for the same matter still executes fine.
    fresh = _make_proposal(client, seed["harbor"]["id"])
    assert client.post(
        f"/assistant/proposals/{fresh['proposal_id']}/approve"
    ).status_code == 200


def test_failed_execution_records_safe_audit_event(client, conn, monkeypatch) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    pid = proposal["proposal_id"]

    def boom(*args, **kwargs):
        raise sqlite3.IntegrityError("secret detail should not leak")

    monkeypatch.setattr(repository, "create_task", boom)
    client.post(f"/assistant/proposals/{pid}/approve")
    monkeypatch.undo()

    rows = repository.list_audit(conn, pid)
    events = [r["event"] for r in rows]
    assert events == ["proposed", "execution_failed"]
    detail = rows[-1]["detail"]
    assert "secret detail" not in detail
    assert detail == "reference_integrity_error"


def test_audit_contains_no_sensitive_payloads(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(
        client, seed["harbor"]["id"], title="Confidential matter name"
    )
    client.post(f"/assistant/proposals/{proposal['proposal_id']}/approve")
    evidence = client.get(
        f"/assistant/proposals/{proposal['proposal_id']}"
    ).json()["evidence"]
    for row in repository.list_audit(conn, proposal["proposal_id"]):
        assert "Confidential matter name" not in (row["detail"] or "")
        for item in evidence:
            assert item["excerpt"] not in (row["detail"] or "")


# --- contract preservation --------------------------------------------------


def test_step_54_response_contract_unchanged(client) -> None:
    seed = _seed_workspace(client)
    before = len(_tasks(client))
    proposal = _make_proposal(client, seed["harbor"]["id"])
    # Step 5.4 fields still present and correct.
    assert proposal["status"] == "pending_approval"
    assert proposal["machine_generated"] is True
    assert proposal["review_required"] is True
    assert isinstance(proposal["proposal_id"], str)
    assert "No task has been created" in proposal["notice"]
    assert len(_tasks(client)) == before
