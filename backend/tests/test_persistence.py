"""Persistence: data written through one connection is visible in later ones."""

from app import repository
from app.db import connect
from app.schemas import ClientCreate, MatterCreate, TaskCreate


def test_data_survives_across_connections(db_path, conn) -> None:
    client_row = repository.create_client(conn, ClientCreate(name="Acme Legal"))
    matter_row = repository.create_matter(
        conn, MatterCreate(client_id=client_row["id"], title="Merger diligence")
    )
    repository.create_task(
        conn, TaskCreate(matter_id=matter_row["id"], title="Collect financials")
    )
    conn.commit()

    # Simulate a restart: brand-new connection to the same file.
    fresh = connect(db_path)
    try:
        assert [r["name"] for r in repository.list_clients(fresh)] == ["Acme Legal"]
        assert [r["title"] for r in repository.list_matters(fresh)] == [
            "Merger diligence"
        ]
        assert [r["title"] for r in repository.list_tasks(fresh)] == [
            "Collect financials"
        ]
    finally:
        fresh.close()


def test_api_data_survives_across_testclient_sessions(db_path, conn) -> None:
    """Two separate app sessions share the same database file (both logged in)."""
    from fastapi.testclient import TestClient

    from app.main import app
    from conftest import TEST_PASSWORD, TEST_USERNAME, login_client, make_user

    make_user(conn)

    with TestClient(app) as first:
        login_client(first, TEST_USERNAME, TEST_PASSWORD)
        assert first.post("/clients", json={"name": "Acme Legal"}).status_code == 201

    with TestClient(app) as second:
        login_client(second, TEST_USERNAME, TEST_PASSWORD)
        listed = second.get("/clients")
        assert listed.status_code == 200
        assert [c["name"] for c in listed.json()] == ["Acme Legal"]
