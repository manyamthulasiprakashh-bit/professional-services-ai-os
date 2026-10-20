"""Smoke tests for the milestone 1 system endpoints."""

from fastapi.testclient import TestClient

from app.main import APP_NAME, APP_VERSION, app

client = TestClient(app)


def test_health_returns_healthy_payload() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": APP_NAME,
        "version": APP_VERSION,
    }


def test_health_content_type_is_json() -> None:
    response = client.get("/health")

    assert response.headers["content-type"].startswith("application/json")


def test_root_describes_application_and_links_to_docs() -> None:
    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["application"] == APP_NAME
    assert payload["version"] == APP_VERSION
    assert payload["docs"] == "/docs"
    assert payload["health"] == "/health"
    assert isinstance(payload["description"], str)
    assert payload["description"].strip()


def test_openapi_schema_is_served() -> None:
    """Guards the /docs link: the schema endpoint must stay available."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == APP_NAME
    assert schema["info"]["version"] == APP_VERSION
    assert "/health" in schema["paths"]
    assert "/" in schema["paths"]
