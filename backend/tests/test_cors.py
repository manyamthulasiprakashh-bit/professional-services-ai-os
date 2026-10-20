"""CORS policy tests: only the local Vite dev origins are allowed.

Credentials (cookies) are enabled, so origins must be exact and never a
wildcard.
"""

from fastapi.testclient import TestClient

from app.main import ALLOWED_HEADERS, ALLOWED_ORIGINS, app

client = TestClient(app)


def test_allowed_origin_gets_cors_headers() -> None:
    response = client.get("/health", headers={"Origin": ALLOWED_ORIGINS[0]})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGINS[0]


def test_disallowed_origin_gets_no_cors_headers() -> None:
    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_credentials_allowed_for_exact_origin_not_wildcard() -> None:
    response = client.options(
        "/clients",
        headers={
            "Origin": ALLOWED_ORIGINS[1],
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGINS[1]
    assert response.headers.get("access-control-allow-credentials") == "true"
    # Credentialed CORS must never be paired with a wildcard origin.
    assert response.headers["access-control-allow-origin"] != "*"


def test_preflight_allows_get_and_post_for_local_dev() -> None:
    response = client.options(
        "/clients",
        headers={
            "Origin": ALLOWED_ORIGINS[1],
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGINS[1]
    allowed = response.headers["access-control-allow-methods"]
    assert "GET" in allowed and "POST" in allowed
    assert "DELETE" not in allowed


def test_allowed_headers_are_explicit_not_wildcard() -> None:
    # No wildcard: only the headers the frontend actually sends.
    assert "*" not in ALLOWED_HEADERS
    assert "Content-Type" in ALLOWED_HEADERS
    assert "X-CSRF-Token" in ALLOWED_HEADERS


def test_preflight_advertises_csrf_and_content_type_headers() -> None:
    response = client.options(
        "/assistant/ask",
        headers={
            "Origin": ALLOWED_ORIGINS[0],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGINS[0]
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed
    assert "x-csrf-token" in allowed


def test_unapproved_origin_preflight_is_not_allowed() -> None:
    response = client.options(
        "/assistant/ask",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert "access-control-allow-origin" not in response.headers

