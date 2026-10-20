"""Tests for client creation, listing and validation."""


def test_create_and_list_client(client) -> None:
    created = client.post("/clients", json={"name": "Acme Legal", "email": "a@acme.test"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Acme Legal"
    assert body["email"] == "a@acme.test"
    assert body["id"] >= 1
    assert body["created_at"]

    listed = client.get("/clients")
    assert listed.status_code == 200
    assert listed.json() == [body]


def test_create_client_without_email(client) -> None:
    created = client.post("/clients", json={"name": "Beta Advisory"})
    assert created.status_code == 201
    assert created.json()["email"] is None


def test_duplicate_client_name_conflicts(client) -> None:
    assert client.post("/clients", json={"name": "Acme Legal"}).status_code == 201
    duplicate = client.post("/clients", json={"name": "ACME LEGAL"})
    assert duplicate.status_code == 409


def test_blank_client_name_rejected(client) -> None:
    response = client.post("/clients", json={"name": "   "})
    assert response.status_code == 422


def test_invalid_email_rejected(client) -> None:
    response = client.post("/clients", json={"name": "Gamma", "email": "not-an-email"})
    assert response.status_code == 422


def test_missing_fields_rejected(client) -> None:
    assert client.post("/clients", json={}).status_code == 422
