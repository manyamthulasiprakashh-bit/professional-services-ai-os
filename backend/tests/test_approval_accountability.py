"""Tests for approval accountability and audit actor attribution (Step 6.2C-3).

Covers: the authenticated actor recorded on approval/rejection, forged body/query
actor fields being ignored, legacy rows keeping NULL actors, migration
repeatability, actor rollback on failure, and unchanged duplicate/concurrent
behavior.
"""

import sqlite3

from app import approval, auth, repository
from app.db import apply_schema, connect
from app.schemas import ClientCreate, MatterCreate, ProposedTask, TaskCreate
from conftest import TEST_USERNAME
from test_assistant_retrieval import _seed_workspace
from test_proposals import FakeAssistant, _proposal_json, _propose


def _make_proposal(client, matter_id: int, **kwargs) -> dict:
    assistant = FakeAssistant(_proposal_json(matter_id=matter_id, **kwargs))
    body = _propose(client, "add a task", assistant).json()
    assert body["proposal"] is not None, body
    return body["proposal"]


def _actor_id(conn) -> int:
    return auth.get_user_by_username(conn, TEST_USERNAME)["id"]


def _audit_rows(conn, proposal_id: str) -> list[dict]:
    return [dict(r) for r in repository.list_audit(conn, proposal_id)]


def _seed_legacy_pending(conn, proposal_id: str = "legacy01") -> None:
    """Create a pending proposal directly in the repository (no actor concept)."""
    client = repository.create_client(conn, ClientCreate(name="Legacy"))
    matter = repository.create_matter(
        conn, MatterCreate(client_id=client["id"], title="Matter")
    )
    payload = ProposedTask(
        title="Legacy task", description="x", matter_id=matter["id"],
        matter_title="Matter",
    )
    canonical = approval.canonical_payload(payload)
    repository.create_proposal(
        conn, proposal_id=proposal_id, action_type="create_task",
        payload_json=canonical, payload_hash=approval.payload_hash(canonical),
        evidence_json="[]", model="fake",
    )
    conn.commit()


# --- success / rejection attribution ----------------------------------------


def test_approval_records_actor_on_proposal_and_audit(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"], title="Attribute me")
    uid = _actor_id(conn)

    response = client.post(f"/assistant/proposals/{proposal['proposal_id']}/approve")
    assert response.status_code == 200
    assert response.json()["status"] == "executed"

    row = repository.get_proposal(conn, proposal["proposal_id"])
    assert row["decided_by_user_id"] == uid

    rows = _audit_rows(conn, proposal["proposal_id"])
    executed = [r for r in rows if r["event"] == "executed"]
    assert len(executed) == 1
    assert executed[0]["actor_user_id"] == uid
    # The `proposed` event is recorded without an accountable actor.
    proposed = [r for r in rows if r["event"] == "proposed"]
    assert proposed[0]["actor_user_id"] is None


def test_actor_is_exposed_read_only_in_record(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    uid = _actor_id(conn)
    client.post(f"/assistant/proposals/{proposal['proposal_id']}/approve")
    record = client.get(f"/assistant/proposals/{proposal['proposal_id']}").json()
    assert record["decided_by_user_id"] == uid


def test_rejection_records_actor_on_proposal_and_audit(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    uid = _actor_id(conn)

    response = client.post(f"/assistant/proposals/{proposal['proposal_id']}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["created_task"] is None

    row = repository.get_proposal(conn, proposal["proposal_id"])
    assert row["status"] == "rejected"
    assert row["decided_by_user_id"] == uid
    rejected = [r for r in _audit_rows(conn, proposal["proposal_id"])
                if r["event"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["actor_user_id"] == uid


# --- forged actor fields are ignored ----------------------------------------


def test_forged_actor_fields_in_body_and_query_are_ignored(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    uid = _actor_id(conn)
    other = auth.create_user(
        conn, username="attacker", password="another-strong-pw-1", role="approver"
    )
    conn.commit()
    assert other["id"] != uid

    response = client.post(
        f"/assistant/proposals/{proposal['proposal_id']}/approve",
        params={"user_id": other["id"], "actor_user_id": other["id"],
                "decided_by_user_id": other["id"]},
        json={"user_id": other["id"], "actor_user_id": other["id"],
              "approved_by": other["id"], "role": "approver"},
    )
    assert response.status_code == 200
    row = repository.get_proposal(conn, proposal["proposal_id"])
    assert row["decided_by_user_id"] == uid
    executed = [r for r in _audit_rows(conn, proposal["proposal_id"])
                if r["event"] == "executed"]
    assert executed[0]["actor_user_id"] == uid


def test_forged_actor_fields_ignored_on_rejection(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    uid = _actor_id(conn)
    response = client.post(
        f"/assistant/proposals/{proposal['proposal_id']}/reject",
        json={"user_id": 999999, "approved_by": 999999, "role": "approver"},
    )
    assert response.status_code == 200
    row = repository.get_proposal(conn, proposal["proposal_id"])
    assert row["decided_by_user_id"] == uid


# --- legacy rows keep NULL actors -------------------------------------------


def test_legacy_rows_keep_null_actor_after_migration(db_path) -> None:
    """A pre-accountability decision keeps NULL actors; no actor is fabricated."""
    conn = connect(db_path)
    _seed_legacy_pending(conn)
    # Decide the legacy way (no accountable actor supplied).
    assert repository.claim_pending_proposal(conn, "legacy01") is True
    task = repository.create_task(
        conn,
        TaskCreate(matter_id=1, title="Legacy task", status="todo", priority="medium"),
    )
    repository.mark_executed(conn, "legacy01", task["id"])
    conn.commit()

    row = repository.get_proposal(conn, "legacy01")
    assert row["decided_by_user_id"] is None
    assert all(e["actor_user_id"] is None for e in _audit_rows(conn, "legacy01"))

    # Re-applying the schema must not invent actors for legacy rows.
    apply_schema(conn)
    conn.commit()
    assert repository.get_proposal(conn, "legacy01")["decided_by_user_id"] is None
    assert all(e["actor_user_id"] is None for e in _audit_rows(conn, "legacy01"))
    conn.close()


# --- migration repeatability ------------------------------------------------


def test_migration_is_repeatable(db_path) -> None:
    conn = connect(db_path)
    apply_schema(conn)
    conn.commit()
    # Applying again must be a no-op (no "duplicate column" error).
    apply_schema(conn)
    apply_schema(conn)
    conn.commit()

    proposal_cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(action_proposals)")}
    audit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(action_audit)")}
    assert "decided_by_user_id" in proposal_cols
    assert "actor_user_id" in audit_cols
    conn.close()


# --- rollback of actor attribution ------------------------------------------


def test_actor_attribution_rolls_back_with_failed_transaction(db_path, monkeypatch) -> None:
    """If execution fails after the claim, the decided actor must not persist."""
    conn = connect(db_path)
    apply_schema(conn)
    _seed_legacy_pending(conn, "rollback1")

    def boom(*args, **kwargs):
        raise sqlite3.IntegrityError("forced failure")

    monkeypatch.setattr(repository, "create_task", boom)
    status, created, error = approval.execute_proposal(
        conn, "rollback1", actor_user_id=42
    )
    conn.close()

    assert status == "failed"
    assert created is None

    verify = connect(db_path)
    row = repository.get_proposal(verify, "rollback1")
    # mark_failed leaves decided_by_user_id unset (NULL): the approved claim and
    # its actor were rolled back with the failed task insertion.
    assert row["status"] == "failed"
    assert row["decided_by_user_id"] is None
    failed = [r for r in _audit_rows(verify, "rollback1")
              if r["event"] == "execution_failed"]
    assert len(failed) == 1
    verify.close()


# --- duplicate / concurrent behavior unchanged ------------------------------


def test_duplicate_approval_actor_unchanged(client, conn) -> None:
    seed = _seed_workspace(client)
    proposal = _make_proposal(client, seed["harbor"]["id"])
    uid = _actor_id(conn)
    pid = proposal["proposal_id"]
    assert client.post(f"/assistant/proposals/{pid}/approve").status_code == 200
    before = len(client.get("/tasks").json())
    second = client.post(f"/assistant/proposals/{pid}/approve")
    assert second.status_code == 409
    assert len(client.get("/tasks").json()) == before
    # The recorded actor stays the first (only) approver.
    assert repository.get_proposal(conn, pid)["decided_by_user_id"] == uid


def test_concurrent_claim_records_single_actor(db_path) -> None:
    conn = connect(db_path)
    apply_schema(conn)
    _seed_legacy_pending(conn, "raceactor")

    winner = connect(db_path)
    loser = connect(db_path)
    assert repository.claim_pending_proposal(winner, "raceactor", actor_user_id=7) is True
    winner.commit()
    assert repository.claim_pending_proposal(loser, "raceactor", actor_user_id=8) is False
    assert repository.get_proposal(winner, "raceactor")["decided_by_user_id"] == 7
    winner.close()
    loser.close()
    conn.close()
