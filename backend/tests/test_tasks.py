"""Tests for task creation, listing, filters and reference validation."""


def _seed_matter(client) -> int:
    client_id = client.post("/clients", json={"name": "Acme Legal"}).json()["id"]
    return client.post(
        "/matters", json={"client_id": client_id, "title": "Merger diligence"}
    ).json()["id"]


def test_create_and_list_tasks(client) -> None:
    matter_id = _seed_matter(client)
    created = client.post(
        "/tasks",
        json={"matter_id": matter_id, "title": "Collect financials", "priority": "high"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["matter_id"] == matter_id
    assert body["status"] == "todo"
    assert body["priority"] == "high"

    listed = client.get("/tasks")
    assert listed.status_code == 200
    assert listed.json() == [body]


def test_task_filters(client) -> None:
    matter_id = _seed_matter(client)
    other = client.post("/clients", json={"name": "Other Co"}).json()["id"]
    other_matter = client.post(
        "/matters", json={"client_id": other, "title": "Other matter"}
    ).json()["id"]

    client.post("/tasks", json={"matter_id": matter_id, "title": "T1"})
    client.post(
        "/tasks",
        json={"matter_id": matter_id, "title": "T2", "status": "done"},
    )
    client.post("/tasks", json={"matter_id": other_matter, "title": "T3"})

    by_matter = client.get("/tasks", params={"matter_id": matter_id})
    assert [t["title"] for t in by_matter.json()] == ["T1", "T2"]

    done = client.get("/tasks", params={"status": "done"})
    assert [t["title"] for t in done.json()] == ["T2"]


def test_missing_matter_reference_returns_404(client) -> None:
    response = client.post("/tasks", json={"matter_id": 999, "title": "Orphan task"})
    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_invalid_task_status_and_priority_rejected(client) -> None:
    matter_id = _seed_matter(client)
    bad_status = client.post(
        "/tasks",
        json={"matter_id": matter_id, "title": "T", "status": "archived"},
    )
    assert bad_status.status_code == 422

    bad_priority = client.post(
        "/tasks",
        json={"matter_id": matter_id, "title": "T", "priority": "urgent"},
    )
    assert bad_priority.status_code == 422
