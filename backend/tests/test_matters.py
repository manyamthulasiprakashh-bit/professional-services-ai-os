"""Tests for matter creation, listing and reference validation."""


def _create_client(client, name="Acme Legal") -> int:
    return client.post("/clients", json={"name": name}).json()["id"]


def test_create_and_list_matters(client) -> None:
    client_id = _create_client(client)
    created = client.post(
        "/matters", json={"client_id": client_id, "title": "Merger diligence"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["client_id"] == client_id
    assert body["status"] == "open"

    listed = client.get("/matters")
    assert listed.status_code == 200
    assert listed.json() == [body]


def test_matter_filter_by_client(client) -> None:
    first = _create_client(client, "Acme Legal")
    second = _create_client(client, "Beta Advisory")
    client.post("/matters", json={"client_id": first, "title": "M1"})
    client.post("/matters", json={"client_id": second, "title": "M2"})

    listed = client.get("/matters", params={"client_id": first})
    assert [m["title"] for m in listed.json()] == ["M1"]


def test_missing_client_reference_returns_404(client) -> None:
    response = client.post(
        "/matters", json={"client_id": 999, "title": "Orphan matter"}
    )
    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_invalid_matter_status_rejected(client) -> None:
    client_id = _create_client(client)
    response = client.post(
        "/matters",
        json={"client_id": client_id, "title": "Bad status", "status": "paused"},
    )
    assert response.status_code == 422


def test_blank_matter_title_rejected(client) -> None:
    client_id = _create_client(client)
    response = client.post(
        "/matters", json={"client_id": client_id, "title": "  "}
    )
    assert response.status_code == 422
